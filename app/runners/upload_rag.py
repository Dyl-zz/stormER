"""Upload / RAG node (CLAUDE.md §9.2): a mini-RAG over one uploaded file.

A STATIC SNAPSHOT — it cannot meaningfully refresh, and the raw input is not
re-fetchable (a user's file cannot be reconstructed).

Two execution shapes, both ordinary node_execution rows:
  - the *indexing execution*: resolved_inputs carries a staging_uri; its raw
    blob is the original file and its rag_chunk rows (written in on_success)
    are the index. Its input_keys.index_execution_id points at ITSELF.
  - *question executions*: each question is a run whose input_keys point at the
    indexing execution's id + content hash.

When the indexing execution's blob is dropped (raw_data_state=unrecoverable)
its chunks die with it and the node can no longer answer new questions — that
state is reported plainly, never silently.
"""

import hashlib
import json
import math
import re
import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.spine.blobstore import BlobStore
from app.spine.models import ExecutionStatus, NodeExecution, RagChunk, RawDataState
from app.spine.models.rag_chunk import EMBEDDING_DIM
from app.spine.runner import NodeRunner, RetentionPolicy, RunnerError, StructuredResult

CHUNK_CHARS = 1200


class RagParams(BaseModel):
    question: str | None = None
    top_k: int = 5


class RagInputKeys(BaseModel):
    index_execution_id: str  # for the indexing run, its own id
    content_hash: str
    filename: str | None = None


class HashEmbedder:
    """Deterministic local embedding: hashed bag-of-words, L2-normalized.
    Deliberately boring — no API dependency, adequate for single-file retrieval.
    Swappable without schema changes; pgvector stores whatever this emits."""

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.md5(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def chunk_text(text: str) -> list[str]:
    """Paragraph-respecting fixed-size chunks."""
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(current) + len(paragraph) + 2 > CHUNK_CHARS and current:
            chunks.append(current)
            current = ""
        while len(paragraph) > CHUNK_CHARS:  # single oversized paragraph
            chunks.append(paragraph[:CHUNK_CHARS])
            paragraph = paragraph[CHUNK_CHARS:]
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


class UploadRagRunner(NodeRunner):
    node_type = "upload_rag"
    params_model = RagParams
    input_keys_model = RagInputKeys
    timeout_seconds = 120
    trust_label = "static snapshot"
    refreshable = False  # document re-run reports it as "snapshot — not refreshed" (§10)
    # Raw input is NOT re-fetchable: keep long, and dropping it is final (§11).
    retention = RetentionPolicy(
        draft_blob_ttl_days=365, milestone_blob_ttl_days=1825, swept_state="unrecoverable"
    )

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        blobstore: BlobStore,
    ) -> None:
        self._session_factory = session_factory
        self._blobstore = blobstore
        self._embedder = HashEmbedder()

    def format_input_keys(self, input_keys: dict) -> str:
        keys = RagInputKeys.model_validate(input_keys)
        name = f"{keys.filename} | " if keys.filename else ""
        return f"{name}index {keys.index_execution_id} | sha256 {keys.content_hash[:12]}…"

    async def run(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        if resolved_inputs.get("staging_uri"):
            return await self._index(parameters, resolved_inputs)
        return await self._answer(parameters, resolved_inputs)

    # --- indexing execution ---------------------------------------------------

    async def _index(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        raw = await self._blobstore.get(resolved_inputs["staging_uri"])
        text = raw.decode("utf-8", errors="replace")
        chunks = chunk_text(text)
        if not chunks:
            raise RunnerError(code="empty_file", message="uploaded file contains no text")
        filename = resolved_inputs.get("filename")
        input_keys = RagInputKeys(
            index_execution_id=str(resolved_inputs["execution_id"]),
            content_hash=hashlib.sha256(raw).hexdigest(),
            filename=filename,
        )
        result = StructuredResult(
            generated_text=(
                f"Indexed {filename or 'uploaded file'}: {len(chunks)} chunks,"
                f" {len(raw)} bytes. Static snapshot — this node cannot refresh;"
                " re-upload to replace it."
            ),
            input_keys=input_keys.model_dump(),
            raw_mime_type=resolved_inputs.get("mime_type", "text/plain"),
        )
        return result, raw

    async def on_success(self, execution_id, result: StructuredResult, raw: bytes) -> None:
        """Persist the chunk index, keyed to the indexing execution (§9.2).
        Question runs have nothing to persist."""
        if result.input_keys.get("index_execution_id") != str(execution_id):
            return
        chunks = chunk_text(raw.decode("utf-8", errors="replace"))
        async with self._session_factory() as session:
            for ordinal, text in enumerate(chunks):
                session.add(
                    RagChunk(
                        execution_id=execution_id,
                        ordinal=ordinal,
                        text=text,
                        embedding=self._embedder.embed(text),
                    )
                )
            await session.commit()

    # --- question execution ---------------------------------------------------

    async def _answer(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        params = RagParams.model_validate(parameters)
        if not params.question:
            raise RunnerError(
                code="missing_question",
                message="set a question in the node parameters, or upload a file to index",
            )
        node_id = uuid.UUID(str(resolved_inputs["node_id"]))

        async with self._session_factory() as session:
            index_execution = await self._latest_index_execution(session, node_id)
            if index_execution is None:
                raise RunnerError(code="no_index", message="no indexed upload on this node yet")
            if index_execution.raw_data_state == RawDataState.unrecoverable:
                raise RunnerError(
                    code="index_unrecoverable",
                    message=(
                        "the uploaded file's raw data was dropped and cannot be"
                        " reconstructed; this node can no longer answer new questions"
                    ),
                )
            chunks = (
                (
                    await session.execute(
                        select(RagChunk)
                        .where(RagChunk.execution_id == index_execution.id)
                        .order_by(RagChunk.ordinal)
                    )
                )
                .scalars()
                .all()
            )
        if not chunks:
            raise RunnerError(
                code="index_missing_chunks",
                message="index has no chunks (interrupted indexing?) — re-upload the file",
            )

        query_vector = self._embedder.embed(params.question)
        scored = sorted(
            ((_cosine(query_vector, list(c.embedding)), c) for c in chunks),
            key=lambda pair: pair[0],
            reverse=True,
        )[: params.top_k]

        retrieved = [
            {"ordinal": c.ordinal, "score": round(score, 4), "text": c.text}
            for score, c in scored
        ]
        passages = "\n\n".join(
            f"[chunk {r['ordinal']}] {r['text']}" for r in retrieved
        )
        generated_text = (
            f"Q: {params.question}\n\nTop {len(retrieved)} passages from the uploaded"
            f" file (static snapshot):\n\n{passages}"
        )
        index_keys = RagInputKeys.model_validate(index_execution.input_keys)
        input_keys = RagInputKeys(
            index_execution_id=str(index_execution.id),
            content_hash=index_keys.content_hash,
            filename=index_keys.filename,
        )
        result = StructuredResult(
            generated_text=generated_text,
            input_keys=input_keys.model_dump(),
            raw_mime_type="application/json",
        )
        raw_blob = json.dumps(
            {"question": params.question, "retrieved": retrieved}, indent=2
        ).encode("utf-8")
        return result, raw_blob

    async def _latest_index_execution(
        self, session: AsyncSession, node_id: uuid.UUID
    ) -> NodeExecution | None:
        rows = (
            (
                await session.execute(
                    select(NodeExecution)
                    .where(
                        NodeExecution.node_id == node_id,
                        NodeExecution.status == ExecutionStatus.succeeded,
                    )
                    .order_by(NodeExecution.created_at.desc(), NodeExecution.id.desc())
                )
            )
            .scalars()
            .all()
        )
        for execution in rows:
            keys = execution.input_keys or {}
            if keys.get("index_execution_id") == str(execution.id):
                return execution
        return None
