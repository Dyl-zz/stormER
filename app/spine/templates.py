"""Templates (CLAUDE.md §7): save-as-template drops executions, ALWAYS.
A template is a structural copy with no FK to node or node_execution."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.models import Branch, Node, Template


async def save_as_template(
    session: AsyncSession, node_id: uuid.UUID, owner_id: uuid.UUID, name: str
) -> Template:
    node = await session.get(Node, node_id)
    if node is None:
        raise LookupError(f"node {node_id} not found")
    branch = await session.get(Branch, node.branch_id)
    template = Template(
        owner_id=owner_id,
        name=name,
        type=node.type,
        parameters=node.parameters,
        declared_inputs=node.declared_inputs,
        color_tag=branch.color if branch else None,
    )
    session.add(template)
    await session.commit()
    return template


async def instantiate_template(
    session: AsyncSession, template_id: uuid.UUID, branch_id: uuid.UUID
) -> Node:
    """Drag-in from the dictionary: mints a new EMPTY node — no executions."""
    template = await session.get(Template, template_id)
    if template is None:
        raise LookupError(f"template {template_id} not found")
    result = await session.execute(
        select(func.max(Node.ordinal)).where(Node.branch_id == branch_id)
    )
    current = result.scalar_one_or_none()
    node = Node(
        branch_id=branch_id,
        type=template.type,
        title=template.name,
        ordinal=0 if current is None else current + 1,
        parameters=template.parameters,
        declared_inputs=template.declared_inputs,
    )
    session.add(node)
    await session.commit()
    return node
