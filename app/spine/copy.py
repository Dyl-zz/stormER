"""Node-copy — the ONLY path by which executions travel (CLAUDE.md §7).

Copies the recipe and, if the source has a latest *succeeded* execution, carries
it across as a new immutable row on the new node. The raw blob is duplicated
under the new execution's key (sharing a URI would entangle the two executions'
sweep fates). If the source has no succeeded execution, the copy is minted empty.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.blobstore import BlobNotFound, BlobStore
from app.spine.executions import latest_execution
from app.spine.models import ExecutionLabel, ExecutionStatus, Node, NodeExecution, RawDataState


async def _next_ordinal(session: AsyncSession, branch_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.max(Node.ordinal)).where(Node.branch_id == branch_id)
    )
    current = result.scalar_one_or_none()
    return 0 if current is None else current + 1


async def copy_node(
    session: AsyncSession,
    node_id: uuid.UUID,
    dest_branch_id: uuid.UUID,
    blobstore: BlobStore,
) -> Node:
    source = await session.get(Node, node_id)
    if source is None:
        raise LookupError(f"node {node_id} not found")

    new_node = Node(
        branch_id=dest_branch_id,
        type=source.type,
        title=source.title,
        ordinal=await _next_ordinal(session, dest_branch_id),
        parameters=source.parameters,
        declared_inputs=source.declared_inputs,
    )
    session.add(new_node)
    await session.flush()  # materialize new_node.id for the carried execution's FK

    carried = await latest_execution(session, node_id)
    if carried is not None:
        new_execution_id = uuid.uuid4()
        new_uri: str | None = None
        new_state = carried.raw_data_state
        if carried.raw_data_state == RawDataState.present and carried.raw_data_uri:
            try:
                data = await blobstore.get(carried.raw_data_uri)
                # Blob FIRST, then the row pointing at it (§6 write ordering).
                new_uri = await blobstore.put(
                    f"executions/{new_execution_id}", data, carried.raw_data_meta or {}
                )
            except BlobNotFound:
                # Source row pointed at a missing blob — the §6 "must not happen"
                # case. Don't propagate the lie to the copy.
                new_state = RawDataState.unrecoverable
        session.add(
            NodeExecution(
                id=new_execution_id,
                node_id=new_node.id,
                status=ExecutionStatus.succeeded,
                label=ExecutionLabel.draft,
                created_at=carried.created_at,
                started_at=carried.started_at,
                finished_at=carried.finished_at,
                generated_text=carried.generated_text,
                input_keys=carried.input_keys,
                raw_data_uri=new_uri,
                raw_data_state=new_state,
                raw_data_meta=carried.raw_data_meta,
            )
        )

    await session.commit()
    return new_node
