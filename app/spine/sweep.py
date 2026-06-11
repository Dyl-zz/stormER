"""Maintenance sweeps (CLAUDE.md §10, §11).

sweep_blobs: retention drives the BLOB sweep, never the rows. Per-node-type
policy comes from the runner (re-fetchability differs); milestones keep their
blobs longer than drafts regardless of type.

sweep_stuck: running executions older than (timeout x 2) are failed with
code=worker_lost so a dead worker never leaves a node locked in-flight forever.
"""

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.blobstore import BlobStore
from app.spine.models import (
    ExecutionLabel,
    ExecutionStatus,
    Node,
    NodeExecution,
    RagChunk,
    RawDataState,
)
from app.spine.runner import get_runner


class SweepReport(BaseModel):
    swept: int = 0
    unrecoverable: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; rows are always written as UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def sweep_blobs(
    session: AsyncSession, blobstore: BlobStore, now: datetime | None = None
) -> SweepReport:
    now = now or _utcnow()
    report = SweepReport()
    rows = await session.execute(
        select(NodeExecution, Node.type)
        .join(Node, NodeExecution.node_id == Node.id)
        .where(NodeExecution.raw_data_state == RawDataState.present)
    )
    for execution, node_type in rows.all():
        policy = get_runner(node_type).retention
        ttl_days = (
            policy.milestone_blob_ttl_days
            if execution.label == ExecutionLabel.milestone
            else policy.draft_blob_ttl_days
        )
        reference = _aware(execution.finished_at or execution.created_at)
        if reference + timedelta(days=ttl_days) > now:
            continue

        if execution.raw_data_uri:
            await blobstore.delete(execution.raw_data_uri)
        execution.raw_data_uri = None
        new_state = RawDataState(policy.swept_state)
        execution.raw_data_state = new_state
        if new_state == RawDataState.unrecoverable:
            # Derived data shares the fate of its execution's raw data (§9.2).
            for chunk in (
                (
                    await session.execute(
                        select(RagChunk).where(RagChunk.execution_id == execution.id)
                    )
                )
                .scalars()
                .all()
            ):
                await session.delete(chunk)
            report.unrecoverable += 1
        else:
            report.swept += 1
    await session.commit()
    return report


async def sweep_stuck(session: AsyncSession, now: datetime | None = None) -> int:
    now = now or _utcnow()
    count = 0
    rows = await session.execute(
        select(NodeExecution, Node.type)
        .join(Node, NodeExecution.node_id == Node.id)
        .where(NodeExecution.status == ExecutionStatus.running)
    )
    for execution, node_type in rows.all():
        timeout = get_runner(node_type).timeout_seconds
        started = _aware(execution.started_at or execution.created_at)
        if started + timedelta(seconds=timeout * 2) > now:
            continue
        execution.error = {
            "code": "worker_lost",
            "message": f"run exceeded {timeout * 2}s with no worker completion",
            "retryable": True,
        }
        execution.finished_at = now
        execution.status = ExecutionStatus.failed
        count += 1
    await session.commit()
    return count
