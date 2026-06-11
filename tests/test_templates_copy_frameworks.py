"""§7: templating and framework-saving drop executions ALWAYS; node-copy is the
only path executions travel, and only the latest succeeded one."""

from sqlalchemy import select

from app.spine.copy import copy_node
from app.spine.executions import enqueue_run
from app.spine.frameworks import collect_bindings, instantiate_framework, save_as_framework
from app.spine.models import Branch, CenterNode, Node, NodeExecution, RawDataState
from app.spine.templates import instantiate_template, save_as_template
from app.worker.tasks import execute_node


async def _run_once(session, blobstore, enqueuer, node_id):
    execution = await enqueue_run(session, node_id, enqueuer)
    await execute_node(session, blobstore, execution.id)
    await session.refresh(execution)
    return execution


async def test_save_as_template_drops_executions(session, user, branch, node, enqueuer, blobstore):
    await _run_once(session, blobstore, enqueuer, node.id)
    template = await save_as_template(session, node.id, user.id, "Risk template")

    assert template.type == "fake"
    assert template.parameters == {"topic": "risk"}
    assert template.color_tag == branch.color
    # Structural copy only: no execution data anywhere on the row.
    assert not hasattr(template, "node_id")
    assert not hasattr(template, "execution_id")


async def test_instantiate_template_mints_empty_node(session, user, branch, node):
    template = await save_as_template(session, node.id, user.id, "Risk template")
    minted = await instantiate_template(session, template.id, branch.id)
    executions = (
        (await session.execute(select(NodeExecution).where(NodeExecution.node_id == minted.id)))
        .scalars()
        .all()
    )
    assert executions == []
    assert minted.ordinal == 1  # appended after the existing node


async def test_node_copy_carries_latest_succeeded_execution(
    session, branch, node, enqueuer, blobstore
):
    execution = await _run_once(session, blobstore, enqueuer, node.id)

    copy = await copy_node(session, node.id, branch.id, blobstore)
    carried = (
        (await session.execute(select(NodeExecution).where(NodeExecution.node_id == copy.id)))
        .scalars()
        .one()
    )
    assert carried.id != execution.id  # a new immutable row, not a shared one
    assert carried.generated_text == execution.generated_text
    assert carried.input_keys == execution.input_keys
    assert carried.raw_data_uri != execution.raw_data_uri  # blob duplicated, not shared
    assert await blobstore.get(carried.raw_data_uri) == await blobstore.get(
        execution.raw_data_uri
    )
    assert carried.raw_data_state == RawDataState.present


async def test_node_copy_without_succeeded_execution_is_empty(session, branch, node, blobstore):
    copy = await copy_node(session, node.id, branch.id, blobstore)
    executions = (
        (await session.execute(select(NodeExecution).where(NodeExecution.node_id == copy.id)))
        .scalars()
        .all()
    )
    assert executions == []


async def test_save_framework_drops_executions_and_center(
    session, user, document, branch, node, enqueuer, blobstore
):
    await _run_once(session, blobstore, enqueuer, node.id)
    center = (
        (await session.execute(select(CenterNode).where(CenterNode.document_id == document.id)))
        .scalars()
        .one()
    )
    center.markdown = "my precious thesis"
    await session.commit()

    framework = await save_as_framework(session, document.id, user.id, "Initiation framework")

    text = str(framework.structure)
    assert "thesis" not in text and "finding" not in text
    branch_spec = framework.structure["branches"][0]
    assert branch_spec["name"] == "Filings" and branch_spec["color"] == branch.color
    assert branch_spec["nodes"][0]["parameters"] == {"topic": "risk"}


async def test_bindings_collected_generically_and_filled(session, user, document, branch, node):
    framework = await save_as_framework(session, document.id, user.id, "F")
    slots = collect_bindings(framework.structure)
    assert [s.field for s in slots] == ["company_ref"]  # the fake runner's binding field

    from app.spine.frameworks import BindingValue

    new_document = await instantiate_framework(
        session,
        framework.id,
        user.id,
        "Other Co",
        [BindingValue(branch_ordinal=0, node_ordinal=0, field="company_ref", value="XYZ")],
    )

    new_branch = (
        (await session.execute(select(Branch).where(Branch.document_id == new_document.id)))
        .scalars()
        .one()
    )
    new_node = (
        (await session.execute(select(Node).where(Node.branch_id == new_branch.id)))
        .scalars()
        .one()
    )
    assert new_node.parameters["company_ref"] == "XYZ"
    assert new_node.parameters["topic"] == "risk"  # non-binding parameters copied as-is
    executions = (
        (await session.execute(select(NodeExecution).where(NodeExecution.node_id == new_node.id)))
        .scalars()
        .all()
    )
    assert executions == []  # instantiation mints EMPTY nodes
    new_center = (
        (await session.execute(select(CenterNode).where(CenterNode.document_id == new_document.id)))
        .scalars()
        .one()
    )
    assert new_center.markdown == ""
