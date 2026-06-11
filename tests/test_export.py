"""§12 export: pure rendering, canonical layout, golden-file locked.
Any intentional format change updates tests/golden/export_basic.txt in the
same commit (§18)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.spine.export import render_txt
from app.spine.models import (
    Branch,
    CenterNode,
    Document,
    ExecutionLabel,
    ExecutionStatus,
    Node,
    NodeExecution,
    RawDataState,
    ReferenceChip,
    User,
)

GOLDEN = Path(__file__).parent / "golden" / "export_basic.txt"
FIXED_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _uuid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


async def build_canonical_document(session) -> uuid.UUID:
    """One document exercising every §12 selection rule: a milestone node, a
    draft-flagged node, a never-run node, and two cited chips."""
    session.add(User(id=_uuid(1), display_name="Test Analyst", email="analyst@test.local"))
    session.add(
        Document(
            id=_uuid(2),
            owner_id=_uuid(1),
            title="Battery Co — Initiation",
            created_at=FIXED_NOW,
        )
    )
    session.add(
        Branch(id=_uuid(3), document_id=_uuid(2), name="Filings", color="#1f77b4", ordinal=0)
    )
    session.add(
        Branch(id=_uuid(4), document_id=_uuid(2), name="Market", color="#ff7f0e", ordinal=1)
    )
    session.add(
        Node(
            id=_uuid(5), branch_id=_uuid(3), type="fake", title="Risk factors", ordinal=0,
            parameters={"topic": "risk"}, declared_inputs={},
        )
    )
    session.add(
        Node(
            id=_uuid(6), branch_id=_uuid(3), type="fake", title="MD&A", ordinal=1,
            parameters={"topic": "mdna"}, declared_inputs={},
        )
    )
    session.add(
        Node(
            id=_uuid(7), branch_id=_uuid(4), type="fake", title="Competitors", ordinal=0,
            parameters={"topic": "competitors"}, declared_inputs={},
        )
    )

    run_date = datetime(2026, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
    session.add(
        NodeExecution(
            id=_uuid(10), node_id=_uuid(5), status=ExecutionStatus.succeeded,
            label=ExecutionLabel.milestone, created_at=run_date, started_at=run_date,
            finished_at=run_date, generated_text="Key risk: lithium prices.\nSecond line.",
            input_keys={"topic": "risk", "fetched": "2026-06-01"},
            raw_data_uri="mem://x", raw_data_state=RawDataState.present, raw_data_meta={},
        )
    )
    session.add(
        NodeExecution(
            id=_uuid(11), node_id=_uuid(6), status=ExecutionStatus.succeeded,
            label=ExecutionLabel.draft, created_at=run_date, started_at=run_date,
            finished_at=run_date, generated_text="Margins improving.",
            input_keys={"topic": "mdna", "fetched": "2026-06-01"},
            raw_data_uri=None, raw_data_state=RawDataState.swept, raw_data_meta={},
        )
    )
    # Node 7 never ran — the export must show the hole, not drop it.

    session.add(CenterNode(id=_uuid(8), document_id=_uuid(2), markdown=(
        "Bull case rests on risk control {{chip:aaaa}} and margins {{chip:bbbb}}.\n"
        "Risk again: {{chip:aaaa}}."
    )))
    session.add(
        ReferenceChip(
            id=_uuid(20), center_node_id=_uuid(8), node_id=_uuid(5),
            execution_id=_uuid(10), marker="aaaa",
        )
    )
    session.add(
        ReferenceChip(
            id=_uuid(21), center_node_id=_uuid(8), node_id=_uuid(6),
            execution_id=_uuid(11), marker="bbbb",
        )
    )
    await session.commit()
    return _uuid(2)


async def test_export_matches_golden_file(session):
    document_id = await build_canonical_document(session)
    rendered = await render_txt(session, document_id, now=FIXED_NOW)
    assert rendered == GOLDEN.read_text()


async def test_export_is_deterministic(session):
    document_id = await build_canonical_document(session)
    first = await render_txt(session, document_id, now=FIXED_NOW)
    second = await render_txt(session, document_id, now=FIXED_NOW)
    assert first == second  # byte-identical: pure function over rows (§12)


async def test_export_contains_trust_and_provenance(session):
    document_id = await build_canonical_document(session)
    rendered = await render_txt(session, document_id, now=FIXED_NOW)
    assert "Trust: test research" in rendered
    assert "| milestone" in rendered
    assert "| draft" in rendered  # the draft flag is inline in the Execution line
    assert "(no findings yet)" in rendered  # the never-run node is visible
    assert "[1]" in rendered and "[2]" in rendered
    assert "{{chip:" not in rendered  # all tokens resolved
