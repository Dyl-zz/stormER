"""Documents, branches, the center node, chips, batch re-run, and export."""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.errors import ApiError
from app.api.serializers import branch_json, chip_json, document_json, node_json
from app.db import get_session
from app.spine.executions import run_document
from app.spine.export import render_txt
from app.spine.frameworks import save_as_framework
from app.spine.models import (
    Branch,
    CenterNode,
    Document,
    ExecutionStatus,
    Node,
    NodeExecution,
    ReferenceChip,
    User,
)
from app.worker.queue import get_enqueuer

router = APIRouter()


async def _document_or_404(session: AsyncSession, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise ApiError(404, "not_found", f"document {document_id} not found")
    return document


async def _center_of(session: AsyncSession, document_id: uuid.UUID) -> CenterNode:
    center = (
        await session.execute(select(CenterNode).where(CenterNode.document_id == document_id))
    ).scalar_one_or_none()
    if center is None:
        raise ApiError(500, "missing_center", "document has no center node")
    return center


class DocumentCreate(BaseModel):
    title: str


@router.post("/documents", status_code=201)
async def create_document(
    body: DocumentCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    document = Document(owner_id=user.id, title=body.title)
    session.add(document)
    await session.flush()  # materialize document.id for the FK below
    # Every document has exactly one center node (§15) — created with it.
    session.add(CenterNode(document_id=document.id))
    await session.commit()
    return document_json(document)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    document = await _document_or_404(session, document_id)
    center = await _center_of(session, document_id)
    branches = (
        (
            await session.execute(
                select(Branch).where(Branch.document_id == document_id).order_by(Branch.ordinal)
            )
        )
        .scalars()
        .all()
    )
    payload = document_json(document)
    payload["center"] = {"id": str(center.id), "markdown": center.markdown}
    payload["branches"] = []
    for branch in branches:
        nodes = (
            (
                await session.execute(
                    select(Node).where(Node.branch_id == branch.id).order_by(Node.ordinal)
                )
            )
            .scalars()
            .all()
        )
        entry = branch_json(branch)
        entry["nodes"] = [node_json(n) for n in nodes]
        payload["branches"].append(entry)
    return payload


class BranchCreate(BaseModel):
    name: str
    color: str


@router.post("/documents/{document_id}/branches", status_code=201)
async def create_branch(
    document_id: uuid.UUID,
    body: BranchCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _document_or_404(session, document_id)
    current = (
        await session.execute(
            select(func.max(Branch.ordinal)).where(Branch.document_id == document_id)
        )
    ).scalar_one_or_none()
    branch = Branch(
        document_id=document_id,
        name=body.name,
        color=body.color,
        ordinal=0 if current is None else current + 1,
    )
    session.add(branch)
    await session.commit()
    return branch_json(branch)


class CenterUpdate(BaseModel):
    markdown: str


@router.put("/documents/{document_id}/center")
async def update_center(
    document_id: uuid.UUID,
    body: CenterUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    center = await _center_of(session, document_id)
    center.markdown = body.markdown
    await session.commit()
    return {"id": str(center.id), "markdown": center.markdown}


class ChipCreate(BaseModel):
    node_id: uuid.UUID
    execution_id: uuid.UUID


@router.post("/documents/{document_id}/chips", status_code=201)
async def create_chip(
    document_id: uuid.UUID,
    body: ChipCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mint a structured reference chip (§8.1). The client embeds the returned
    token in the center markdown — references are never plain prose."""
    center = await _center_of(session, document_id)
    execution = await session.get(NodeExecution, body.execution_id)
    if execution is None or execution.node_id != body.node_id:
        raise ApiError(404, "not_found", "execution not found on that node")
    if execution.status != ExecutionStatus.succeeded:
        raise ApiError(422, "not_citable", "only succeeded executions can be cited")
    chip = ReferenceChip(
        center_node_id=center.id,
        node_id=body.node_id,
        execution_id=body.execution_id,
        marker=uuid.uuid4().hex,
    )
    session.add(chip)
    await session.commit()
    return chip_json(chip)


@router.post("/documents/{document_id}/run", status_code=202)
async def run_whole_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    enqueuer=Depends(get_enqueuer),
) -> dict:
    """The refresh button (§10): 202 + created execution ids + skipped list
    with reasons. The client polls each execution individually."""
    await _document_or_404(session, document_id)
    report = await run_document(session, document_id, enqueuer)
    return report.model_dump(mode="json")


@router.get("/documents/{document_id}/export")
async def export_document(
    document_id: uuid.UUID,
    format: str = "txt",
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    if format != "txt":
        raise ApiError(422, "unsupported_format", "the MVP exports plain text only (§12)")
    await _document_or_404(session, document_id)
    return PlainTextResponse(await render_txt(session, document_id))


class FrameworkSave(BaseModel):
    name: str


@router.post("/documents/{document_id}/save-framework", status_code=201)
async def save_framework(
    document_id: uuid.UUID,
    body: FrameworkSave,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    await _document_or_404(session, document_id)
    framework = await save_as_framework(session, document_id, user.id, body.name)
    return {"id": str(framework.id), "name": framework.name, "structure": framework.structure}
