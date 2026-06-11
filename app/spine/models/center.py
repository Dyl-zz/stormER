import uuid

from sqlalchemy import ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CenterNode(Base):
    """The thesis the analyst writes. "Mostly content" — NOT a node type and NOT a
    recipe (CLAUDE.md §3.5). References to evidence nodes are chip rows, never prose.

    Chips are embedded in the markdown as `{{chip:<chip_id>}}` tokens; the export
    renderer resolves them to [n] markers (§12)."""

    __tablename__ = "center_node"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document.id"), unique=True)
    markdown: Mapped[str] = mapped_column(Text, default="")
