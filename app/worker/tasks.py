"""ARQ worker (CLAUDE.md §9, §10): selects a runner by node type, enforces the
per-type timeout, writes the blob FIRST, then completes the execution row.

execute_node owns the one permitted mutation window on an execution; run_node
is the thin ARQ wrapper around it.
"""

import asyncio
import uuid

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session_factory
from app.spine.blobstore import BlobStore, LocalBlobStore, blob_meta
from app.spine.executions import complete_execution, fail_execution, mark_running
from app.spine.models import ExecutionStatus, Node, NodeExecution
from app.spine.runner import RunnerError, get_runner
from app.spine.sweep import sweep_blobs, sweep_stuck


async def execute_node(
    session: AsyncSession,
    blobstore: BlobStore,
    execution_id: uuid.UUID,
    resolved_inputs: dict | None = None,
) -> None:
    execution = await session.get(NodeExecution, execution_id)
    if execution is None:
        return  # row gone; nothing to do
    if execution.status != ExecutionStatus.queued:
        return  # duplicate delivery; the row is already past us
    node = await session.get(Node, execution.node_id)
    await mark_running(session, execution)

    try:
        runner = get_runner(node.type)
        parameters = runner.validate_parameters(node.parameters)
        inputs = {
            **(resolved_inputs or {}),
            "node_id": str(node.id),
            "execution_id": str(execution.id),
        }
        result, raw = await asyncio.wait_for(
            runner.run(parameters, inputs), timeout=runner.timeout_seconds
        )
        runner.validate_input_keys(result.input_keys)

        # Write ordering (§6): blob FIRST, then the row pointing at it.
        meta = blob_meta(raw, result.raw_mime_type)
        uri = await blobstore.put(f"executions/{execution.id}", raw, meta)
        await complete_execution(session, execution, result, uri, meta)
        await runner.on_success(execution.id, result, raw)
    except RunnerError as exc:
        await fail_execution(
            session, execution, code=exc.code, message=exc.message, retryable=exc.retryable
        )
    except asyncio.TimeoutError:
        await fail_execution(
            session,
            execution,
            code="timeout",
            message=f"run exceeded the {node.type} timeout",
            retryable=True,
        )
    except Exception as exc:  # noqa: BLE001 — structured error, never a bare traceback
        await fail_execution(
            session,
            execution,
            code="internal_error",
            message=f"{type(exc).__name__}: {exc}",
            retryable=False,
        )


async def run_node(ctx: dict, execution_id: str, resolved_inputs: dict | None = None) -> None:
    async with get_session_factory()() as session:
        await execute_node(session, ctx["blobstore"], uuid.UUID(execution_id), resolved_inputs)


async def sweep_blobs_task(ctx: dict) -> None:
    async with get_session_factory()() as session:
        await sweep_blobs(session, ctx["blobstore"])


async def sweep_stuck_task(ctx: dict) -> None:
    async with get_session_factory()() as session:
        await sweep_stuck(session)


async def startup(ctx: dict) -> None:
    from app.runners import register_all

    settings = get_settings()
    blobstore = LocalBlobStore(settings.blob_dir)
    register_all(settings, get_session_factory(), blobstore)
    ctx["blobstore"] = blobstore


class WorkerSettings:
    functions = [run_node]
    cron_jobs = [
        cron(sweep_stuck_task, minute={0, 15, 30, 45}),
        cron(sweep_blobs_task, hour={3}, minute={0}),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
