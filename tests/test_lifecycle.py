"""Execution lifecycle invariants (CLAUDE.md §6, §10): one in-flight per node,
failures write rows, write ordering, promotion as the sole label mutation."""

import pytest

from app.spine.executions import (
    InFlightConflict,
    PromoteError,
    enqueue_run,
    latest_execution,
    promote,
    run_document,
)
from app.spine.models import (
    ExecutionLabel,
    ExecutionStatus,
    Node,
    NodeExecution,
    RawDataState,
)
from app.spine.runner import RunnerError, StructuredResult
from app.worker.tasks import execute_node
from tests.conftest import FakeRunner


async def test_run_writes_queued_row_and_enqueues_job(session, node, enqueuer):
    execution = await enqueue_run(session, node.id, enqueuer)
    assert execution.status == ExecutionStatus.queued
    assert execution.label == ExecutionLabel.draft
    assert enqueuer.jobs == [(execution.id, None)]


async def test_one_in_flight_per_node(session, node, enqueuer):
    first = await enqueue_run(session, node.id, enqueuer)
    with pytest.raises(InFlightConflict) as exc_info:
        await enqueue_run(session, node.id, enqueuer)
    assert exc_info.value.execution_id == first.id
    assert len(enqueuer.jobs) == 1  # no second job ever reached the queue


async def test_enqueue_failure_marks_execution_failed(session, node, enqueuer):
    enqueuer.fail = True
    with pytest.raises(ConnectionError):
        await enqueue_run(session, node.id, enqueuer)
    row = (await session.execute(NodeExecution.__table__.select())).one()
    assert row.status == "failed"
    assert row.error["code"] == "enqueue_failed"
    assert row.error["retryable"] is True


async def test_successful_run_blob_before_row(session, node, enqueuer, blobstore):
    execution = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, execution.id)

    await session.refresh(execution)
    assert execution.status == ExecutionStatus.succeeded
    assert execution.generated_text == "finding about risk"
    assert execution.input_keys == {"topic": "risk", "fetched": "2026-01-01"}
    assert execution.raw_data_state == RawDataState.present
    assert execution.raw_data_meta["mime_type"] == "application/octet-stream"
    # The blob the row points at must actually exist, and have been put first.
    assert await blobstore.get(execution.raw_data_uri) == b"raw data for risk"
    assert blobstore.events[0] == ("put", execution.raw_data_uri)


async def test_failed_run_writes_structured_error_row(session, node, enqueuer, blobstore, runners):
    class FailingRunner(FakeRunner):
        async def run(self, parameters, resolved_inputs):
            raise RunnerError(code="upstream_down", message="no data", retryable=True)

    runners["fake"] = FailingRunner()
    execution = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, execution.id)

    await session.refresh(execution)
    assert execution.status == ExecutionStatus.failed
    assert execution.error == {"code": "upstream_down", "message": "no data", "retryable": True}
    assert execution.raw_data_uri is None
    assert execution.finished_at is not None
    # A failed run frees the node: a new run is accepted.
    await enqueue_run(session, node.id, enqueuer)


async def test_empty_input_keys_is_a_failure(session, node, enqueuer, blobstore, runners):
    class KeylessRunner(FakeRunner):
        async def run(self, parameters, resolved_inputs):
            return StructuredResult(generated_text="x", input_keys={}), b"raw"

    runners["fake"] = KeylessRunner()
    execution = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, execution.id)
    await session.refresh(execution)
    assert execution.status == ExecutionStatus.failed  # §6: empty input_keys is a bug


async def test_latest_execution_means_latest_succeeded(session, node, enqueuer, blobstore, runners):
    first = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, first.id)

    class FailingRunner(FakeRunner):
        async def run(self, parameters, resolved_inputs):
            raise RunnerError(code="boom", message="boom")

    runners["fake"] = FailingRunner()
    failed = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, failed.id)

    latest = await latest_execution(session, node.id)
    assert latest is not None and latest.id == first.id  # failed run is never "latest"


async def test_promote_is_the_save_gesture(session, node, enqueuer, blobstore):
    execution = await enqueue_run(session, node.id, enqueuer)

    with pytest.raises(PromoteError):
        await promote(session, execution.id)  # in-flight: not promotable

    await execute_node(session, blobstore, execution.id)
    promoted = await promote(session, execution.id)
    assert promoted.label == ExecutionLabel.milestone


async def test_run_document_skips_with_reasons(session, branch, node, enqueuer):
    snapshot_node = Node(
        branch_id=branch.id, type="fake_snapshot", title="Upload", ordinal=1,
        parameters={}, declared_inputs={},
    )
    busy_node = Node(
        branch_id=branch.id, type="fake", title="Busy", ordinal=2,
        parameters={}, declared_inputs={},
    )
    session.add_all([snapshot_node, busy_node])
    await session.commit()
    await enqueue_run(session, busy_node.id, enqueuer)  # make it in-flight

    report = await run_document(session, branch.document_id, enqueuer)

    assert len(report.created) == 1  # only the free, refreshable node
    reasons = {s.node_id: s.reason for s in report.skipped}
    assert reasons[snapshot_node.id] == "snapshot"
    assert reasons[busy_node.id] == "in_flight"
