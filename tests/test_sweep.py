"""§11 retention: blobs swept by per-type policy, rows kept forever; §10 stuck
runs failed as worker_lost; §9.2 chunk fate follows the indexing blob."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.spine.executions import enqueue_run
from app.spine.models import ExecutionStatus, Node, NodeExecution, RagChunk, RawDataState
from app.spine.sweep import sweep_blobs, sweep_stuck
from app.worker.tasks import execute_node


async def _succeeded_execution(session, blobstore, enqueuer, node_id):
    execution = await enqueue_run(session, node_id, enqueuer)
    await execute_node(session, blobstore, execution.id)
    await session.refresh(execution)
    assert execution.status == ExecutionStatus.succeeded
    return execution


async def test_draft_blob_swept_after_ttl_row_kept(session, node, enqueuer, blobstore):
    execution = await _succeeded_execution(session, blobstore, enqueuer, node.id)
    uri = execution.raw_data_uri

    # Inside the 7-day draft TTL: nothing happens.
    report = await sweep_blobs(session, blobstore, now=datetime.now(timezone.utc))
    assert report.swept == 0

    report = await sweep_blobs(
        session, blobstore, now=datetime.now(timezone.utc) + timedelta(days=8)
    )
    assert report.swept == 1
    await session.refresh(execution)
    assert execution.raw_data_uri is None
    assert execution.raw_data_state == RawDataState.swept
    assert uri not in blobstore.blobs
    # The audit trail survives the sweep: text + input_keys forever.
    assert execution.generated_text == "finding about risk"
    assert execution.input_keys["topic"] == "risk"


async def test_milestone_blob_outlives_draft_ttl(session, node, enqueuer, blobstore):
    from app.spine.executions import promote

    execution = await _succeeded_execution(session, blobstore, enqueuer, node.id)
    await promote(session, execution.id)

    report = await sweep_blobs(
        session, blobstore, now=datetime.now(timezone.utc) + timedelta(days=8)
    )
    assert report.swept == 0  # milestones keep their blob far longer (§11)
    await session.refresh(execution)
    assert execution.raw_data_state == RawDataState.present


async def test_unrecoverable_sweep_deletes_chunks(session, branch, enqueuer, blobstore):
    snapshot_node = Node(
        branch_id=branch.id, type="fake_snapshot", title="Upload", ordinal=1,
        parameters={}, declared_inputs={},
    )
    session.add(snapshot_node)
    await session.commit()
    execution = await _succeeded_execution(session, blobstore, enqueuer, snapshot_node.id)
    session.add(RagChunk(execution_id=execution.id, ordinal=0, text="chunk", embedding=[0.0]))
    await session.commit()

    report = await sweep_blobs(
        session, blobstore, now=datetime.now(timezone.utc) + timedelta(days=31)
    )
    assert report.unrecoverable == 1
    await session.refresh(execution)
    assert execution.raw_data_state == RawDataState.unrecoverable
    chunks = (
        (await session.execute(select(RagChunk).where(RagChunk.execution_id == execution.id)))
        .scalars()
        .all()
    )
    assert chunks == []  # derived data shares the blob's fate (§9.2)


async def test_stuck_running_marked_worker_lost(session, node, enqueuer):
    from app.spine.executions import mark_running

    execution = await enqueue_run(session, node.id, enqueuer)
    await mark_running(session, execution)

    # Within timeout*2: untouched.
    assert await sweep_stuck(session, now=datetime.now(timezone.utc)) == 0

    count = await sweep_stuck(
        session, now=datetime.now(timezone.utc) + timedelta(seconds=21)
    )
    assert count == 1
    await session.refresh(execution)
    assert execution.status == ExecutionStatus.failed
    assert execution.error["code"] == "worker_lost"
    assert execution.error["retryable"] is True
    # The node is free again.
    await enqueue_run(session, node.id, enqueuer)
