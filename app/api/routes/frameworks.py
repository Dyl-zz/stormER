"""Framework endpoints (§7): inspect binding slots, instantiate as a new document."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.serializers import document_json
from app.db import get_session
from app.api.deps import get_current_user
from app.spine.frameworks import BindingValue, collect_bindings, instantiate_framework
from app.spine.models import Framework, User

router = APIRouter()


async def _framework_or_404(session: AsyncSession, framework_id: uuid.UUID) -> Framework:
    framework = await session.get(Framework, framework_id)
    if framework is None:
        raise ApiError(404, "not_found", f"framework {framework_id} not found")
    return framework


@router.get("/frameworks/{framework_id}/bindings")
async def get_bindings(
    framework_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    """The binding slots to prompt for at instantiation — collected generically;
    the spine never knows what the fields mean (§7)."""
    framework = await _framework_or_404(session, framework_id)
    return {"bindings": [b.model_dump() for b in collect_bindings(framework.structure)]}


class FrameworkInstantiate(BaseModel):
    title: str
    bindings: list[BindingValue] = []


@router.post("/frameworks/{framework_id}/instantiate", status_code=201)
async def instantiate(
    framework_id: uuid.UUID,
    body: FrameworkInstantiate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """New document, all branches, EMPTY nodes, bindings filled. Bindings are
    optional — empty slots can be filled per node later (§7 delivery guard)."""
    await _framework_or_404(session, framework_id)
    document = await instantiate_framework(
        session, framework_id, user.id, body.title, body.bindings
    )
    return document_json(document)
