"""Export / Present (CLAUDE.md §12): a pure rendering over existing rows.

Same document state in, byte-identical file out (the timestamp is injected so
callers and the golden-file test control it). No export table, no stored state.

Spine code: this module mentions documents/branches/nodes/executions, never
companies or filings. Equity flavor enters only via generated_text and the
runner-provided input-keys formatter (the ONE runner touchpoint, via interface).
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.spine.models import (
    Branch,
    CenterNode,
    Document,
    ExecutionLabel,
    ExecutionStatus,
    Node,
    NodeExecution,
    ReferenceChip,
    User,
)
from app.spine.runner import get_runner

HEAVY_RULE = "=" * 64
LIGHT_RULE = "-" * 64
CHIP_TOKEN = re.compile(r"\{\{chip:([0-9a-fA-F-]+)\}\}")


async def _selected_execution(session: AsyncSession, node_id: uuid.UUID) -> NodeExecution | None:
    """§12 selection: latest milestone; else latest succeeded draft."""
    for labels in ((ExecutionLabel.milestone,), (ExecutionLabel.milestone, ExecutionLabel.draft)):
        result = await session.execute(
            select(NodeExecution)
            .where(
                NodeExecution.node_id == node_id,
                NodeExecution.status == ExecutionStatus.succeeded,
                NodeExecution.label.in_(labels),
            )
            .order_by(NodeExecution.created_at.desc(), NodeExecution.id.desc())
            .limit(1)
        )
        execution = result.scalar_one_or_none()
        if execution is not None:
            return execution
    return None


def _run_date(execution: NodeExecution) -> str:
    moment = execution.finished_at or execution.created_at
    return moment.date().isoformat()


async def render_txt(
    session: AsyncSession, document_id: uuid.UUID, now: datetime | None = None
) -> str:
    document = await session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document {document_id} not found")
    owner = await session.get(User, document.owner_id)
    center = (
        await session.execute(select(CenterNode).where(CenterNode.document_id == document_id))
    ).scalar_one_or_none()

    exported_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Thesis: resolve chip tokens to [n] in order of first appearance ------
    markdown = center.markdown if center else ""
    chips_by_marker: dict[str, ReferenceChip] = {}
    if center is not None:
        chips = (
            (
                await session.execute(
                    select(ReferenceChip).where(ReferenceChip.center_node_id == center.id)
                )
            )
            .scalars()
            .all()
        )
        chips_by_marker = {c.marker: c for c in chips}

    numbered: dict[str, int] = {}
    references: list[ReferenceChip] = []

    def _substitute(match: re.Match) -> str:
        marker = match.group(1)
        chip = chips_by_marker.get(marker)
        if chip is None:
            return "[?]"  # token with no chip row — render visibly, never silently
        if marker not in numbered:
            numbered[marker] = len(numbered) + 1
            references.append(chip)
        return f"[{numbered[marker]}]"

    thesis = CHIP_TOKEN.sub(_substitute, markdown).strip() or "(no thesis written yet)"

    lines: list[str] = [
        HEAVY_RULE,
        f"RESEARCH REPORT: {document.title}",
        f"Exported: {exported_at}    Analyst: {owner.display_name if owner else '(unknown)'}",
        HEAVY_RULE,
        "",
        "THESIS",
        LIGHT_RULE,
        thesis,
        "",
        HEAVY_RULE,
        "EVIDENCE BRANCHES",
        HEAVY_RULE,
    ]

    branches = (
        (
            await session.execute(
                select(Branch).where(Branch.document_id == document_id).order_by(Branch.ordinal)
            )
        )
        .scalars()
        .all()
    )
    for branch in branches:
        lines += ["", f"BRANCH: {branch.name}", LIGHT_RULE]
        nodes = (
            (
                await session.execute(
                    select(Node).where(Node.branch_id == branch.id).order_by(Node.ordinal)
                )
            )
            .scalars()
            .all()
        )
        for node in nodes:
            runner = get_runner(node.type)
            execution = await _selected_execution(session, node.id)
            lines.append(f"  NODE: {node.title}  [{node.type}]")
            lines.append(f"  Trust: {runner.trust_label}")
            if execution is None:
                # Failed-only and never-run nodes are never silently dropped —
                # the analyst must see the hole in their research (§12).
                lines.append("  Execution: (none)")
                lines.append("  Finding:")
                lines.append("    (no findings yet)")
            else:
                lines.append(
                    f"  Execution: {execution.id} | run {_run_date(execution)}"
                    f" | {execution.label.value}"
                )
                lines.append(f"  Inputs: {runner.format_input_keys(execution.input_keys or {})}")
                lines.append("  Finding:")
                for text_line in (execution.generated_text or "").splitlines() or [""]:
                    lines.append(f"    {text_line}")
            lines.append("")
        if nodes:
            lines.pop()  # branch separation comes from the next header's leading blank

    lines += ["", HEAVY_RULE, "REFERENCES", LIGHT_RULE]
    if references:
        for index, chip in enumerate(references, start=1):
            node = await session.get(Node, chip.node_id)
            execution = await session.get(NodeExecution, chip.execution_id)
            title = node.title if node else "(deleted node)"
            if execution is not None:
                lines.append(
                    f"[{index}] {title} — execution {execution.id}, run {_run_date(execution)}"
                )
            else:
                lines.append(f"[{index}] {title} — execution (deleted)")
    else:
        lines.append("(no references)")
    lines.append(HEAVY_RULE)

    return "\n".join(lines) + "\n"
