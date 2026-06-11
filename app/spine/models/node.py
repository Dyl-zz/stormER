import uuid

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, json_column_type


class Node(Base):
    """A recipe, never results (CLAUDE.md §3.1). Type + parameters + declared
    inputs + branch membership. Results live exclusively in node_execution."""

    __tablename__ = "node"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    branch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("branch.id"))
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(500))
    ordinal: Mapped[int] = mapped_column(Integer)
    parameters: Mapped[dict] = mapped_column(json_column_type(), default=dict)
    # Reserved (CLAUDE.md §8.3): what inputs this node needs, for future sharing.
    declared_inputs: Mapped[dict] = mapped_column(json_column_type(), default=dict)
