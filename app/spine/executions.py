"""Execution lifecycle (CLAUDE.md §6, §10).

Every run writes a node_execution row — successes and failures alike. The row is
created `queued` by the API path, mutated exactly once by the worker (running ->
succeeded|failed), and is frozen afterwards except for label promotion and the
blob sweep.
"""

import uuid
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.models import (
    Branch,
    ExecutionLabel,
    ExecutionStatus,
    Node,
    NodeExecution,
    RawDataState,
)
from app.spine.runner import StructuredResult, get_runner

IN_FLIGHT = (ExecutionStatus.queued, ExecutionStatus.running)


class InFlightConflict(Exception):
    """A second run was requested while one is queued/running (§10 -> API 409)."""

    def __init__(self, node_id: uuid.UUID, execution_id: uuid.UUID) -> None:
        super().__init__(f"node {node_id} already has in-flight execution {execution_id}")
        self.node_id = node_id
        self.execution_id = execution_id


class PromoteError(Exception):
    """Only succeeded executions can become milestones."""


class JobEnqueuer(Protocol):
    """How the spine hands work to the queue without importing ARQ.
    resolved_inputs is small per-run context (e.g. a staged-upload pointer),
    NEVER raw data — raw bytes go through the BlobStore."""

    async def enqueue_run_node(
        self, execution_id: uuid.UUID, resolved_inputs: dict | None = None
    ) -> None: ...


class SkippedNode(BaseModel):
    node_id: uuid.UUID
    reason: str  # "snapshot" | "in_flight"


class RunReport(BaseModel):
    """Result of a document-level re-run (§10): created execution ids plus the
    skipped list with reasons — skips are surfaced, never silent."""

    created: list[uuid.UUID]
    skipped: list[SkippedNode]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def latest_execution(session: AsyncSession, node_id: uuid.UUID) -> NodeExecution | None:
    """THE defined term (§6): most recent by created_at with status=succeeded.
    Failed and in-flight executions are never "latest"."""
    result = await session.execute(
        select(NodeExecution)
        .where(
            NodeExecution.node_id == node_id,
            NodeExecution.status == ExecutionStatus.succeeded,
        )
        .order_by(NodeExecution.created_at.desc(), NodeExecution.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _in_flight_execution(session: AsyncSession, node_id: uuid.UUID) -> NodeExecution | None:
    result = await session.execute(
        select(NodeExecution).where(
            NodeExecution.node_id == node_id,
            NodeExecution.status.in_(IN_FLIGHT),
        )
    )
    return result.scalars().first()


async def _create_queued(session: AsyncSession, node_id: uuid.UUID) -> NodeExecution:
    """Insert a queued row. The partial unique index is the concurrency guard;
    a savepoint keeps an IntegrityError from poisoning the outer transaction."""
    execution = NodeExecution(node_id=node_id, status=ExecutionStatus.queued)
    try:
        async with session.begin_nested():
            session.add(execution)
    except IntegrityError:
        in_flight = await _in_flight_execution(session, node_id)
        in_flight_id = in_flight.id if in_flight else uuid.UUID(int=0)
        raise InFlightConflict(node_id, in_flight_id) from None
    return execution


async def _dispatch(
    session: AsyncSession,
    enqueuer: JobEnqueuer,
    execution: NodeExecution,
    resolved_inputs: dict | None = None,
) -> None:
    """Hand a committed queued row to the worker. If the queue itself is down,
    the row must not sit queued forever — record the failure on it."""
    try:
        await enqueuer.enqueue_run_node(execution.id, resolved_inputs)
    except Exception as exc:  # noqa: BLE001 — any queue failure gets recorded
        await fail_execution(
            session, execution, code="enqueue_failed", message=str(exc), retryable=True
        )
        await session.commit()
        raise


async def enqueue_run(
    session: AsyncSession,
    node_id: uuid.UUID,
    enqueuer: JobEnqueuer,
    resolved_inputs: dict | None = None,
) -> NodeExecution:
    """Run one node: queued row first (committed), then the job. 409 path raises
    InFlightConflict carrying the in-flight execution's id."""
    execution = await _create_queued(session, node_id)
    await session.commit()
    await _dispatch(session, enqueuer, execution, resolved_inputs)
    return execution


async def run_document(
    session: AsyncSession, document_id: uuid.UUID, enqueuer: JobEnqueuer
) -> RunReport:
    """The refresh button (§10): one execution per runnable node. Non-refreshable
    nodes are skipped as "snapshot", busy nodes as "in_flight" — a busy node never
    409s the batch. No new status machinery: the client polls each execution."""
    nodes = (
        (
            await session.execute(
                select(Node)
                .join(Branch, Node.branch_id == Branch.id)
                .where(Branch.document_id == document_id)
                .order_by(Branch.ordinal, Node.ordinal)
            )
        )
        .scalars()
        .all()
    )

    created: list[NodeExecution] = []
    skipped: list[SkippedNode] = []
    for node in nodes:
        if not get_runner(node.type).refreshable:
            skipped.append(SkippedNode(node_id=node.id, reason="snapshot"))
            continue
        try:
            created.append(await _create_queued(session, node.id))
        except InFlightConflict:
            skipped.append(SkippedNode(node_id=node.id, reason="in_flight"))
    await session.commit()

    for execution in created:
        await _dispatch(session, enqueuer, execution)
    return RunReport(created=[e.id for e in created], skipped=skipped)


async def promote(session: AsyncSession, execution_id: uuid.UUID) -> NodeExecution:
    """The save gesture (§11): promotes an existing execution to milestone.
    It never creates one, and it is the sole label mutation."""
    execution = await session.get(NodeExecution, execution_id)
    if execution is None:
        raise LookupError(f"execution {execution_id} not found")
    if execution.status != ExecutionStatus.succeeded:
        raise PromoteError("only succeeded executions can be promoted to milestone")
    execution.label = ExecutionLabel.milestone
    await session.commit()
    return execution


# --- Worker-side transitions (the ONE permitted mutation window, §10) ---------


async def mark_running(session: AsyncSession, execution: NodeExecution) -> None:
    if execution.status != ExecutionStatus.queued:
        raise ValueError(f"cannot start execution in status {execution.status}")
    execution.status = ExecutionStatus.running
    execution.started_at = utcnow()
    await session.commit()


async def complete_execution(
    session: AsyncSession,
    execution: NodeExecution,
    result: StructuredResult,
    raw_data_uri: str,
    raw_data_meta: dict,
) -> None:
    """Freeze a successful run. Caller must have written the blob already —
    a row pointing at a missing blob is the bad case (§6 write ordering)."""
    if execution.status != ExecutionStatus.running:
        raise ValueError(f"cannot complete execution in status {execution.status}")
    if not raw_data_uri:
        raise ValueError("write ordering violation: blob must be written before the row")
    if not result.input_keys:
        raise ValueError("execution completed with empty input_keys — runner bug (§6)")
    execution.generated_text = result.generated_text
    execution.input_keys = result.input_keys
    execution.raw_data_uri = raw_data_uri
    execution.raw_data_state = RawDataState.present
    execution.raw_data_meta = raw_data_meta
    execution.finished_at = utcnow()
    execution.status = ExecutionStatus.succeeded
    await session.commit()


async def fail_execution(
    session: AsyncSession,
    execution: NodeExecution,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    """Failures write rows too (§3.8) — structured, never a bare traceback."""
    if execution.status in (ExecutionStatus.succeeded, ExecutionStatus.failed):
        raise ValueError(f"cannot fail execution in status {execution.status}")
    execution.error = {"code": code, "message": message, "retryable": retryable}
    execution.finished_at = utcnow()
    execution.status = ExecutionStatus.failed
    await session.commit()
