"""Test fixtures: in-memory SQLite (same models via type variants), an
in-memory blob store that records write ordering, a fake job queue, and a
deliberately boring fake runner so spine tests never touch the network."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.spine.blobstore import BlobNotFound, BlobStore
from app.spine.models import Branch, CenterNode, Document, Node, User
from app.spine.runner import (
    NodeRunner,
    RetentionPolicy,
    StructuredResult,
    _registry,
    register_runner,
)
from pydantic import BaseModel, Field


class MemoryBlobStore(BlobStore):
    """Records event order so write-ordering tests can assert blob-before-row."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.events: list[tuple[str, str]] = []

    async def put(self, key: str, data: bytes, meta: dict) -> str:
        uri = f"mem://{key}"
        self.blobs[uri] = data
        self.events.append(("put", uri))
        return uri

    async def get(self, uri: str) -> bytes:
        if uri not in self.blobs:
            raise BlobNotFound(uri)
        return self.blobs[uri]

    async def delete(self, uri: str) -> None:
        self.blobs.pop(uri, None)
        self.events.append(("delete", uri))


class FakeEnqueuer:
    def __init__(self) -> None:
        self.jobs: list[tuple[uuid.UUID, dict | None]] = []
        self.fail = False

    async def enqueue_run_node(
        self, execution_id: uuid.UUID, resolved_inputs: dict | None = None
    ) -> None:
        if self.fail:
            raise ConnectionError("queue is down")
        self.jobs.append((execution_id, resolved_inputs))


class FakeParams(BaseModel):
    topic: str = "anything"
    company_ref: str | None = Field(default=None, json_schema_extra={"binding": True})


class FakeInputKeys(BaseModel):
    topic: str
    fetched: str


class FakeRunner(NodeRunner):
    """Spine tests run against this — the spine must never need a real vertical."""

    node_type = "fake"
    params_model = FakeParams
    input_keys_model = FakeInputKeys
    timeout_seconds = 10
    trust_label = "test research"
    refreshable = True
    retention = RetentionPolicy(
        draft_blob_ttl_days=7, milestone_blob_ttl_days=365, swept_state="swept"
    )

    async def run(self, parameters: dict, resolved_inputs: dict) -> tuple[StructuredResult, bytes]:
        topic = parameters.get("topic", "anything")
        result = StructuredResult(
            generated_text=f"finding about {topic}",
            input_keys={"topic": topic, "fetched": "2026-01-01"},
        )
        return result, f"raw data for {topic}".encode()


class SnapshotRunner(FakeRunner):
    node_type = "fake_snapshot"
    refreshable = False
    retention = RetentionPolicy(
        draft_blob_ttl_days=30, milestone_blob_ttl_days=365, swept_state="unrecoverable"
    )


@pytest_asyncio.fixture
async def session_factory():
    # StaticPool: every session shares the one in-memory database.
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def blobstore():
    return MemoryBlobStore()


@pytest.fixture
def enqueuer():
    return FakeEnqueuer()


@pytest.fixture(autouse=True)
def runners():
    """Fresh registry per test: the two fake runners only."""
    saved = dict(_registry)
    _registry.clear()
    register_runner(FakeRunner())
    register_runner(SnapshotRunner())
    yield _registry
    _registry.clear()
    _registry.update(saved)


@pytest_asyncio.fixture
async def user(session):
    row = User(display_name="Test Analyst", email="analyst@test.local")
    session.add(row)
    await session.commit()
    return row


@pytest_asyncio.fixture
async def document(session, user):
    doc = Document(owner_id=user.id, title="Test Co — Initiation")
    session.add(doc)
    await session.flush()
    session.add(CenterNode(document_id=doc.id))
    await session.commit()
    return doc


@pytest_asyncio.fixture
async def branch(session, document):
    row = Branch(document_id=document.id, name="Filings", color="#1f77b4", ordinal=0)
    session.add(row)
    await session.commit()
    return row


@pytest_asyncio.fixture
async def node(session, branch):
    row = Node(
        branch_id=branch.id,
        type="fake",
        title="Risk factors",
        ordinal=0,
        parameters={"topic": "risk"},
        declared_inputs={},
    )
    session.add(row)
    await session.commit()
    return row
