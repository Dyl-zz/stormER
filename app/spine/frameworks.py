"""Frameworks (CLAUDE.md §7): a saved document structure — branches + recipes,
no executions, no center content, NO FKs to live rows.

Binding parameters are harvested generically from each node type's parameters
model (fields marked binding=True). The spine never knows what a CIK is.
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.models import Branch, CenterNode, Document, Framework, Node
from app.spine.runner import binding_fields, get_runner


class BindingField(BaseModel):
    """One binding slot in a framework, addressed by structural position."""

    branch_ordinal: int
    node_ordinal: int
    node_title: str
    field: str


class BindingValue(BaseModel):
    branch_ordinal: int
    node_ordinal: int
    field: str
    value: Any


async def save_as_framework(
    session: AsyncSession, document_id: uuid.UUID, owner_id: uuid.UUID, name: str
) -> Framework:
    """Serialize structure only. Drops executions and center content, ALWAYS."""
    branches = (
        (
            await session.execute(
                select(Branch).where(Branch.document_id == document_id).order_by(Branch.ordinal)
            )
        )
        .scalars()
        .all()
    )
    structure: dict = {"branches": []}
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
        structure["branches"].append(
            {
                "name": branch.name,
                "color": branch.color,
                "ordinal": branch.ordinal,
                "nodes": [
                    {
                        "type": n.type,
                        "title": n.title,
                        "ordinal": n.ordinal,
                        "parameters": n.parameters,
                        "declared_inputs": n.declared_inputs,
                    }
                    for n in nodes
                ],
            }
        )
    framework = Framework(owner_id=owner_id, name=name, structure=structure)
    session.add(framework)
    await session.commit()
    return framework


def collect_bindings(structure: dict) -> list[BindingField]:
    """Harvest binding slots across all nodes so instantiation can prompt the
    user once (§7). Purely structural — field meanings live in the runners."""
    slots: list[BindingField] = []
    for branch in structure.get("branches", []):
        for node in branch.get("nodes", []):
            params_model = get_runner(node["type"]).params_model
            for field in binding_fields(params_model):
                slots.append(
                    BindingField(
                        branch_ordinal=branch["ordinal"],
                        node_ordinal=node["ordinal"],
                        node_title=node["title"],
                        field=field,
                    )
                )
    return slots


async def instantiate_framework(
    session: AsyncSession,
    framework_id: uuid.UUID,
    owner_id: uuid.UUID,
    title: str,
    bindings: list[BindingValue] | None = None,
) -> Document:
    """New document with all branches and EMPTY nodes (no executions), binding
    values written into the new nodes' parameter slots. Bindings are optional by
    design (§7 delivery guard): empty slots can be filled per node later."""
    framework = await session.get(Framework, framework_id)
    if framework is None:
        raise LookupError(f"framework {framework_id} not found")

    bound: dict[tuple[int, int, str], Any] = {
        (b.branch_ordinal, b.node_ordinal, b.field): b.value for b in (bindings or [])
    }

    document = Document(owner_id=owner_id, title=title)
    session.add(document)
    await session.flush()  # materialize document.id for the FKs below
    session.add(CenterNode(document_id=document.id, markdown=""))
    for branch_spec in framework.structure.get("branches", []):
        branch = Branch(
            document_id=document.id,
            name=branch_spec["name"],
            color=branch_spec["color"],
            ordinal=branch_spec["ordinal"],
        )
        session.add(branch)
        await session.flush()  # materialize branch.id
        for node_spec in branch_spec.get("nodes", []):
            parameters = dict(node_spec.get("parameters") or {})
            for field in binding_fields(get_runner(node_spec["type"]).params_model):
                key = (branch_spec["ordinal"], node_spec["ordinal"], field)
                if key in bound:
                    parameters[field] = bound[key]
            session.add(
                Node(
                    branch_id=branch.id,
                    type=node_spec["type"],
                    title=node_spec["title"],
                    ordinal=node_spec["ordinal"],
                    parameters=parameters,
                    declared_inputs=node_spec.get("declared_inputs") or {},
                )
            )
    await session.commit()
    return document
