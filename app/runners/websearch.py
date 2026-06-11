"""Web-search node (CLAUDE.md §9.3): AI latent web search via the Anthropic API.

The least reproducible node type — DRAFT RESEARCH, and labeled as such. Sources
are always stored (in input_keys and the raw blob) and always displayed; results
are kept longer because they cannot be cleanly re-fetched.
"""

import json
from datetime import datetime, timezone

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.config import Settings
from app.spine.runner import NodeRunner, RetentionPolicy, RunnerError, StructuredResult


class WebSearchParams(BaseModel):
    query: str


class WebSearchInputKeys(BaseModel):
    query: str
    source_urls: list[str]  # MUST be stored for verification (§9.3)
    retrieved_at: str


class WebSearchRunner(NodeRunner):
    node_type = "websearch"
    params_model = WebSearchParams
    input_keys_model = WebSearchInputKeys
    timeout_seconds = 90  # tighter than EDGAR (§10)
    trust_label = "draft research"
    refreshable = True
    # Results are inspectable evidence and cannot be re-fetched as-was (§11).
    retention = RetentionPolicy(
        draft_blob_ttl_days=90, milestone_blob_ttl_days=730, swept_state="unrecoverable"
    )

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.anthropic_api_key
        self._model = settings.anthropic_model

    def format_input_keys(self, input_keys: dict) -> str:
        keys = WebSearchInputKeys.model_validate(input_keys)
        return (
            f"query \"{keys.query}\" | retrieved {keys.retrieved_at}"
            f" | {len(keys.source_urls)} source(s)"
        )

    async def run(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        params = WebSearchParams.model_validate(parameters)
        if not self._api_key:
            raise RunnerError(
                code="missing_api_key",
                message="web search needs RC_ANTHROPIC_API_KEY; this node cannot run without it",
                retryable=False,
            )

        client = AsyncAnthropic(api_key=self._api_key)
        response = await client.messages.create(
            model=self._model,
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Research the following and answer concisely with sources: "
                        f"{params.query}"
                    ),
                }
            ],
        )

        answer_parts: list[str] = []
        source_urls: list[str] = []
        for block in response.content:
            if block.type == "text":
                answer_parts.append(block.text)
                for citation in getattr(block, "citations", None) or []:
                    url = getattr(citation, "url", None)
                    if url and url not in source_urls:
                        source_urls.append(url)
            elif block.type == "web_search_tool_result":
                for item in getattr(block, "content", None) or []:
                    url = getattr(item, "url", None)
                    if url and url not in source_urls:
                        source_urls.append(url)

        answer = "\n".join(answer_parts).strip()
        if not answer:
            raise RunnerError(
                code="empty_answer", message="web search returned no answer text", retryable=True
            )

        retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sources_block = (
            "\n".join(f"  - {url}" for url in source_urls) if source_urls else "  (none reported)"
        )
        input_keys = WebSearchInputKeys(
            query=params.query, source_urls=source_urls, retrieved_at=retrieved_at
        )
        result = StructuredResult(
            generated_text=(
                f"{answer}\n\nDRAFT RESEARCH — verify before citing."
                f"\nSOURCES:\n{sources_block}"
            ),
            input_keys=input_keys.model_dump(),
            raw_mime_type="application/json",
        )
        raw_blob = json.dumps(response.model_dump(mode="json"), indent=2).encode("utf-8")
        return result, raw_blob
