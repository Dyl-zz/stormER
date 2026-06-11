"""The NodeRunner interface and registry — the generalization seam (CLAUDE.md §9).

New node types implement a runner + a parameters model + an input_keys model and
touch nothing else. The spine never imports a concrete runner; runners register
themselves at startup and the spine looks them up by node type.

This module must stay equity-blind (§5): no company, filing, CIK, or EDGAR
concepts here.
"""

from abc import ABC, abstractmethod
from typing import ClassVar, Protocol

from pydantic import BaseModel


class StructuredResult(BaseModel):
    """What a runner returns alongside the raw blob: the small, durable part of
    an execution. input_keys must already be validated by the runner's model."""

    generated_text: str
    input_keys: dict
    raw_mime_type: str = "application/octet-stream"


class RunnerError(Exception):
    """A runner-declared failure, recorded as the execution's structured error."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class RetentionPolicy(BaseModel):
    """Per-type blob retention (§11). Rows are forever; only blobs are swept."""

    draft_blob_ttl_days: int
    milestone_blob_ttl_days: int
    swept_state: str  # "swept" (re-fetchable from input_keys) or "unrecoverable"


class InputKeysFormatter(Protocol):
    """Export's ONE touchpoint into runners (§12): render input_keys for humans."""

    def format_input_keys(self, input_keys: dict) -> str: ...


class NodeRunner(ABC):
    """One method that matters: parameters + resolved inputs in,
    (structured_result, raw_blob) out. The API never calls this directly —
    the worker does, after the job is enqueued (§9)."""

    node_type: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]
    input_keys_model: ClassVar[type[BaseModel]]
    timeout_seconds: ClassVar[int]  # no global default — every runner declares its own (§10)
    trust_label: ClassVar[str]  # shown to the analyst and in export, e.g. "citable research"
    refreshable: ClassVar[bool]  # False => skipped by document re-run as "snapshot" (§10)
    retention: ClassVar[RetentionPolicy]

    @abstractmethod
    async def run(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        """Execute the recipe. Raise RunnerError for expected failures."""

    async def on_success(self, execution_id, result: StructuredResult, raw: bytes) -> None:
        """Called by the worker after the execution row is committed. Runners
        that maintain derived data keyed to the execution (e.g. a RAG chunk
        index) persist it here. Default: nothing."""

    def format_input_keys(self, input_keys: dict) -> str:
        keys = self.input_keys_model.model_validate(input_keys)
        return ", ".join(f"{k}={v}" for k, v in keys.model_dump().items() if v is not None)

    def validate_parameters(self, parameters: dict) -> dict:
        return self.params_model.model_validate(parameters).model_dump(mode="json")

    def validate_input_keys(self, input_keys: dict) -> dict:
        """An execution with unvalidated or empty input_keys is a bug (§6)."""
        return self.input_keys_model.model_validate(input_keys).model_dump(mode="json")


_registry: dict[str, NodeRunner] = {}


def register_runner(runner: NodeRunner) -> None:
    _registry[runner.node_type] = runner


def get_runner(node_type: str) -> NodeRunner:
    if node_type not in _registry:
        raise KeyError(f"no runner registered for node type {node_type!r}")
    return _registry[node_type]


def binding_fields(params_model: type[BaseModel]) -> list[str]:
    """Fields a per-type parameters model marks as bindings (§7) — collected
    generically; the spine never knows what the fields mean."""
    return [
        name
        for name, field in params_model.model_fields.items()
        if (field.json_schema_extra or {}).get("binding") is True
    ]
