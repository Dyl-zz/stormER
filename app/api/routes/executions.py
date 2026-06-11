"""Execution endpoints (§13): enqueue (202/409), poll, keyset history, promote.
There is no PUT/PATCH on executions — promotion is the sole mutation."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.serializers import execution_json
from app.db import get_session
from app.spine.executions import InFlightConflict, PromoteError, enqueue_run, promote
from app.spine.models import Node, NodeExecution
from app.worker.queue import get_enqueuer

router = APIRouter()


@router.post("/nodes/{node_id}/executions", status_code=202)
async def run_node_endpoint(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    enqueuer=Depends(get_enqueuer),
) -> dict:
    if await session.get(Node, node_id) is None:
        raise ApiError(404, "not_found", f"node {node_id} not found")
    try:
        execution = await enqueue_run(session, node_id, enqueuer)
    except InFlightConflict as exc:
        raise ApiError(
            409,
            "execution_in_flight",
            "node already has an in-flight execution",
            {"execution_id": str(exc.execution_id)},
        )
    return execution_json(execution)


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    execution = await session.get(NodeExecution, execution_id)
    if execution is None:
        raise ApiError(404, "not_found", f"execution {execution_id} not found")
    return execution_json(execution)


@router.get("/nodes/{node_id}/executions")
async def list_executions(
    node_id: uuid.UUID,
    after: str | None = None,
    limit: int = Query(default=20, le=100, ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Keyset pagination on (created_at, id), newest first (§13). Offset
    pagination is forbidden here — this table grows forever by design."""
    if await session.get(Node, node_id) is None:
        raise ApiError(404, "not_found", f"node {node_id} not found")
    query = select(NodeExecution).where(NodeExecution.node_id == node_id)
    if after is not None:
        try:
            created_raw, _, id_raw = after.partition(",")
            after_created = datetime.fromisoformat(created_raw)
            after_id = uuid.UUID(id_raw)
        except ValueError:
            raise ApiError(422, "bad_cursor", "after must be '<created_at_iso>,<execution_id>'")
        query = query.where(
            or_(
                NodeExecution.created_at < after_created,
                and_(NodeExecution.created_at == after_created, NodeExecution.id < after_id),
            )
        )
    rows = (
        (
            await session.execute(
                query.order_by(NodeExecution.created_at.desc(), NodeExecution.id.desc()).limit(
                    limit
                )
            )
        )
        .scalars()
        .all()
    )
    next_cursor = (
        f"{rows[-1].created_at.isoformat()},{rows[-1].id}" if len(rows) == limit else None
    )
    return {"items": [execution_json(e) for e in rows], "next": next_cursor}


@router.post("/executions/{execution_id}/promote")
async def promote_execution(
    execution_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    """The save gesture (§11): draft -> milestone."""
    try:
        execution = await promote(session, execution_id)
    except LookupError:
        raise ApiError(404, "not_found", f"execution {execution_id} not found")
    except PromoteError as exc:
        raise ApiError(422, "not_promotable", str(exc))
    return execution_json(execution)
