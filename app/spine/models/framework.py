import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, json_column_type


class Framework(Base):
    """A saved document *structure*: branches + recipes, serialized to JSONB
    (CLAUDE.md §7). No executions, no center content, NO FKs to live rows.

    structure = {"branches": [{"name", "color", "ordinal",
                               "nodes": [{"type", "title", "ordinal",
                                          "parameters", "declared_inputs"}]}]}
    """

    __tablename__ = "framework"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("user.id"))
    # Reserved for the governance layer (§8.4). Nothing reads it in the MVP.
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    name: Mapped[str] = mapped_column(String(500))
    structure: Mapped[dict] = mapped_column(json_column_type())
