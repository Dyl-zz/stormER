import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReferenceChip(Base):
    """A structured citation (CLAUDE.md §8.1). Pins node AND execution so future
    staleness detection knows exactly which run was cited. Never plain prose."""

    __tablename__ = "reference_chip"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    center_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("center_node.id"))
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("node.id"))
    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("node_execution.id"))
    # The token embedded in the center markdown: `{{chip:<marker>}}`.
    marker: Mapped[str] = mapped_column(String(64))
