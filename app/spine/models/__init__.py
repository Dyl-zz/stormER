"""All spine tables. Importing this module registers every model on Base.metadata."""

from app.spine.models.branch import Branch
from app.spine.models.center import CenterNode
from app.spine.models.chip import ReferenceChip
from app.spine.models.document import Document
from app.spine.models.execution import (
    ExecutionLabel,
    ExecutionStatus,
    NodeExecution,
    RawDataState,
)
from app.spine.models.framework import Framework
from app.spine.models.node import Node
from app.spine.models.rag_chunk import RagChunk
from app.spine.models.template import Template
from app.spine.models.user import User

__all__ = [
    "Branch",
    "CenterNode",
    "Document",
    "ExecutionLabel",
    "ExecutionStatus",
    "Framework",
    "Node",
    "NodeExecution",
    "RagChunk",
    "RawDataState",
    "ReferenceChip",
    "Template",
    "User",
]
