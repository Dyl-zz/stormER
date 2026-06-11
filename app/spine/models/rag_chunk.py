import uuid

from sqlalchemy import JSON, ForeignKey, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

try:  # pgvector column on Postgres; JSON list on SQLite (tests)
    from pgvector.sqlalchemy import Vector

    _embedding_type = JSON().with_variant(Vector(256), "postgresql")
except ImportError:  # pragma: no cover
    _embedding_type = JSON()

EMBEDDING_DIM = 256


class RagChunk(Base):
    """Derived index data for an Upload/RAG *indexing execution* (CLAUDE.md §9.2).
    Shares the fate of that execution's raw blob: deleted when raw_data_state
    becomes `unrecoverable`."""

    __tablename__ = "rag_chunk"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("node_execution.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(_embedding_type)
