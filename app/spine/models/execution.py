import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, json_column_type


class ExecutionStatus(str, enum.Enum):
    """One-way transitions only: queued -> running -> succeeded | failed (§10)."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ExecutionLabel(str, enum.Enum):
    draft = "draft"
    milestone = "milestone"


class RawDataState(str, enum.Enum):
    present = "present"
    swept = "swept"  # blob dropped, re-fetchable from input_keys
    unrecoverable = "unrecoverable"  # blob dropped, cannot be reconstructed


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, native_enum=False, values_callable=lambda x: [m.value for m in x])


class NodeExecution(Base):
    """One immutable run of a recipe (CLAUDE.md §6, §10). Small, kept forever.

    The only mutations ever permitted:
      - the worker's single completion pass (status/timestamps/results),
      - label promotion (draft -> milestone),
      - the blob sweep (raw_data_uri/raw_data_state).
    """

    __tablename__ = "node_execution"
    __table_args__ = (
        # §10: at most one in-flight execution per node, enforced in the schema.
        Index(
            "uq_one_inflight_per_node",
            "node_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_execution_node_created", "node_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("node.id"))
    status: Mapped[ExecutionStatus] = mapped_column(
        _enum(ExecutionStatus, "execution_status"), default=ExecutionStatus.queued
    )
    label: Mapped[ExecutionLabel] = mapped_column(
        _enum(ExecutionLabel, "execution_label"), default=ExecutionLabel.draft
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Durable, re-fetchable identity of the inputs used. Validated by the
    # runner's per-type input_keys model before the row is completed (§6).
    input_keys: Mapped[dict | None] = mapped_column(json_column_type(), nullable=True)
    # Structured failure: {"code": ..., "message": ..., "retryable": bool}. Never a bare traceback.
    error: Mapped[dict | None] = mapped_column(json_column_type(), nullable=True)

    raw_data_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_data_state: Mapped[RawDataState | None] = mapped_column(
        _enum(RawDataState, "raw_data_state"), nullable=True
    )
    raw_data_meta: Mapped[dict | None] = mapped_column(json_column_type(), nullable=True)
