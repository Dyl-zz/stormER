"""Nodes (recipes — freely editable, §13), node-copy, templates, and uploads."""

import uuid

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_blobstore, get_current_user
from app.api.errors import ApiError
from app.api.serializers import execution_json, node_json
from app.db import get_session
from app.spine.blobstore import BlobStore
from app.spine.copy import copy_node
from app.spine.executions import InFlightConflict, enqueue_run
from app.spine.models import Branch, Node, Template, User
from app.spine.runner import get_runner
from app.spine.templates import instantiate_template, save_as_template
from app.worker.queue import get_enqueuer

router = APIRouter()


async def _node_or_404(session: AsyncSession, node_id: uuid.UUID) -> Node:
    node = await session.get(Node, node_id)
    if node is None:
        raise ApiError(404, "not_found", f"node {node_id} not found")
    return node


def _validated_parameters(node_type: str, parameters: dict) -> dict:
    try:
        runner = get_runner(node_type)
    except KeyError:
        raise ApiError(422, "unknown_node_type", f"no runner for node type {node_type!r}")
    try:
        return runner.validate_parameters(parameters)
    except ValidationError as exc:
        raise ApiError(422, "invalid_parameters", "parameters failed validation", exc.errors())


class NodeCreate(BaseModel):
    type: str
    title: str
    parameters: dict = {}
    declared_inputs: dict = {}


@router.post("/branches/{branch_id}/nodes", status_code=201)
async def create_node(
    branch_id: uuid.UUID,
    body: NodeCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if await session.get(Branch, branch_id) is None:
        raise ApiError(404, "not_found", f"branch {branch_id} not found")
    current = (
        await session.execute(select(func.max(Node.ordinal)).where(Node.branch_id == branch_id))
    ).scalar_one_or_none()
    node = Node(
        branch_id=branch_id,
        type=body.type,
        title=body.title,
        ordinal=0 if current is None else current + 1,
        parameters=_validated_parameters(body.type, body.parameters),
        declared_inputs=body.declared_inputs,
    )
    session.add(node)
    await session.commit()
    return node_json(node)


class NodeUpdate(BaseModel):
    title: str | None = None
    parameters: dict | None = None


@router.patch("/nodes/{node_id}")
async def update_node(
    node_id: uuid.UUID,
    body: NodeUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Recipes are freely editable; editing a recipe never touches executions."""
    node = await _node_or_404(session, node_id)
    if body.title is not None:
        node.title = body.title
    if body.parameters is not None:
        node.parameters = _validated_parameters(node.type, body.parameters)
    await session.commit()
    return node_json(node)


class NodeCopy(BaseModel):
    dest_branch_id: uuid.UUID


@router.post("/nodes/{node_id}/copy", status_code=201)
async def copy_node_endpoint(
    node_id: uuid.UUID,
    body: NodeCopy,
    session: AsyncSession = Depends(get_session),
    blobstore: BlobStore = Depends(get_blobstore),
) -> dict:
    """Node-copy: the ONLY path by which executions travel (§7)."""
    await _node_or_404(session, node_id)
    if await session.get(Branch, body.dest_branch_id) is None:
        raise ApiError(404, "not_found", f"branch {body.dest_branch_id} not found")
    return node_json(await copy_node(session, node_id, body.dest_branch_id, blobstore))


class TemplateSave(BaseModel):
    name: str


@router.post("/nodes/{node_id}/save-template", status_code=201)
async def save_template(
    node_id: uuid.UUID,
    body: TemplateSave,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Save-as-template drops executions, ALWAYS (§7)."""
    await _node_or_404(session, node_id)
    template = await save_as_template(session, node_id, user.id, body.name)
    return {
        "id": str(template.id),
        "name": template.name,
        "type": template.type,
        "parameters": template.parameters,
        "declared_inputs": template.declared_inputs,
        "color_tag": template.color_tag,
    }


class TemplateInstantiate(BaseModel):
    branch_id: uuid.UUID


@router.post("/templates/{template_id}/instantiate", status_code=201)
async def instantiate_template_endpoint(
    template_id: uuid.UUID,
    body: TemplateInstantiate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if await session.get(Template, template_id) is None:
        raise ApiError(404, "not_found", f"template {template_id} not found")
    if await session.get(Branch, body.branch_id) is None:
        raise ApiError(404, "not_found", f"branch {body.branch_id} not found")
    return node_json(await instantiate_template(session, template_id, body.branch_id))


@router.post("/nodes/{node_id}/upload", status_code=202)
async def upload_to_node(
    node_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    blobstore: BlobStore = Depends(get_blobstore),
    enqueuer=Depends(get_enqueuer),
) -> dict:
    """Stage the file in blob storage, then enqueue an execution whose resolved
    inputs point at the staged bytes. Raw bytes never ride the job queue."""
    node = await _node_or_404(session, node_id)
    data = await file.read()
    if not data:
        raise ApiError(422, "empty_file", "uploaded file is empty")
    staging_uri = await blobstore.put(f"staging/{uuid.uuid4()}", data, {})
    try:
        execution = await enqueue_run(
            session,
            node.id,
            enqueuer,
            resolved_inputs={
                "staging_uri": staging_uri,
                "filename": file.filename,
                "mime_type": file.content_type or "application/octet-stream",
            },
        )
    except InFlightConflict as exc:
        raise ApiError(
            409,
            "execution_in_flight",
            "node already has an in-flight execution",
            {"execution_id": str(exc.execution_id)},
        )
    return execution_json(execution)
