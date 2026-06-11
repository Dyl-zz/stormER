import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, json_column_type


class Template(Base):
    """One serialized recipe in the library (CLAUDE.md §7). A structural copy —
    deliberately NO foreign key to node or node_execution, ever."""

    __tablename__ = "template"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("user.id"))
    # Reserved for the governance layer (§8.4). Nothing reads it in the MVP.
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    name: Mapped[str] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(50))
    parameters: Mapped[dict] = mapped_column(json_column_type(), default=dict)
    declared_inputs: Mapped[dict] = mapped_column(json_column_type(), default=dict)
    color_tag: Mapped[str | None] = mapped_column(String(32), nullable=True)
