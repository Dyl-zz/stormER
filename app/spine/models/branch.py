import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Branch(Base):
    """A branch of evidence nodes. Carries the color — color IS branch membership
    (CLAUDE.md §3.4); there is no other color concept anywhere."""

    __tablename__ = "branch"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_branch_ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document.id"))
    name: Mapped[str] = mapped_column(String(200))
    color: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer)
