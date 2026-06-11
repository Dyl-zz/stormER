"""Runner unit tests: the pure EDGAR parsing helpers, and the Upload/RAG
index-then-question flow end to end through the worker path."""

import pytest

from app.config import Settings
from app.runners.edgar import EdgarParams, _extract_section, _strip_html
from app.runners.upload_rag import HashEmbedder, UploadRagRunner, chunk_text
from app.spine.executions import enqueue_run
from app.spine.models import ExecutionStatus, Node, RagChunk
from app.spine.runner import register_runner
from app.worker.tasks import execute_node
from sqlalchemy import select


def test_strip_html_drops_tags_and_scripts():
    raw = "<html><script>evil()</script><p>Item 1A. Risk Factors</p><div>text</div></html>"
    text = _strip_html(raw)
    assert "evil" not in text
    assert "Item 1A. Risk Factors" in text and "text" in text


def test_extract_section_skips_table_of_contents():
    text = (
        "Item 1A. Risk Factors .... 12\n"  # the TOC mention
        "filler\n"
        "Item 1A. Risk Factors\nLithium prices may fall.\n"
        "Item 1B. Unresolved Staff Comments\nnone"
    )
    section = _extract_section(text, "risk_factors")
    assert section is not None
    assert "Lithium prices may fall." in section
    assert "Unresolved Staff Comments" not in section


def test_edgar_params_validation():
    params = EdgarParams(cik="0000320193")
    assert params.cik == "320193"  # normalized, leading zeros dropped
    with pytest.raises(ValueError):
        EdgarParams(cik="AAPL")
    with pytest.raises(ValueError):
        EdgarParams(cik="320193", section="liquidity")


def test_chunking_and_embedding_are_deterministic():
    chunks = chunk_text(("paragraph one\n\n" * 3) + ("x" * 3000))
    assert all(len(c) <= 1200 for c in chunks)
    embedder = HashEmbedder()
    a, b = embedder.embed("lithium supply risk"), embedder.embed("lithium supply risk")
    assert a == b
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9  # L2-normalized


async def test_upload_rag_index_then_question(session_factory, session, branch, enqueuer, blobstore):
    runner = UploadRagRunner(Settings(), session_factory, blobstore)
    register_runner(runner)
    node = Node(
        branch_id=branch.id, type="upload_rag", title="Expert notes", ordinal=1,
        parameters={}, declared_inputs={},
    )
    session.add(node)
    await session.commit()

    # 1. Index: stage a file, run the indexing execution.
    file_body = (
        "Lithium supply is constrained through 2027.\n\n"
        "Battery margins depend on cathode prices.\n\n"
        "The company holds 12 patents on solid-state design."
    ).encode()
    staging_uri = await blobstore.put("staging/test", file_body, {})
    index_execution = await enqueue_run(
        session, node.id, enqueuer,
        resolved_inputs={"staging_uri": staging_uri, "filename": "notes.txt"},
    )
    await execute_node(
        session, blobstore, index_execution.id,
        {"staging_uri": staging_uri, "filename": "notes.txt"},
    )
    await session.refresh(index_execution)
    assert index_execution.status == ExecutionStatus.succeeded, index_execution.error
    assert index_execution.input_keys["index_execution_id"] == str(index_execution.id)
    assert "Static snapshot" in index_execution.generated_text

    chunks = (
        (
            await session.execute(
                select(RagChunk).where(RagChunk.execution_id == index_execution.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) >= 1  # the derived index exists, keyed to the execution

    # 2. Question: a normal execution whose input_keys point at the index.
    node.parameters = {"question": "what about lithium supply?", "top_k": 2}
    await session.commit()
    question_execution = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, question_execution.id)
    await session.refresh(question_execution)
    assert question_execution.status == ExecutionStatus.succeeded, question_execution.error
    assert question_execution.input_keys["index_execution_id"] == str(index_execution.id)
    assert "Lithium supply" in question_execution.generated_text


async def test_question_without_index_fails_plainly(session_factory, session, branch, enqueuer, blobstore):
    runner = UploadRagRunner(Settings(), session_factory, blobstore)
    register_runner(runner)
    node = Node(
        branch_id=branch.id, type="upload_rag", title="Notes", ordinal=2,
        parameters={"question": "anything?"}, declared_inputs={},
    )
    session.add(node)
    await session.commit()

    execution = await enqueue_run(session, node.id, enqueuer)
    await execute_node(session, blobstore, execution.id)
    await session.refresh(execution)
    assert execution.status == ExecutionStatus.failed
    assert execution.error["code"] == "no_index"
