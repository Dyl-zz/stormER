"""API conventions (§13): async job pattern, 409/404/422 in the one envelope,
keyset pagination, promote as the only execution mutation."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_blobstore, get_current_user
from app.db import get_session
from app.main import create_app
from app.worker.queue import get_enqueuer


@pytest_asyncio.fixture
async def client(session, user, blobstore, enqueuer):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_blobstore] = lambda: blobstore
    app.dependency_overrides[get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[get_current_user] = lambda: user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


async def test_document_branch_node_flow(client):
    doc = (await client.post("/documents", json={"title": "Test Co"})).json()
    branch = (
        await client.post(
            f"/documents/{doc['id']}/branches", json={"name": "Filings", "color": "#123456"}
        )
    ).json()
    node_response = await client.post(
        f"/branches/{branch['id']}/nodes",
        json={"type": "fake", "title": "Risk", "parameters": {"topic": "risk"}},
    )
    assert node_response.status_code == 201

    full = (await client.get(f"/documents/{doc['id']}")).json()
    assert full["branches"][0]["color"] == "#123456"
    assert full["branches"][0]["nodes"][0]["title"] == "Risk"


async def test_run_node_202_then_409(client, node):
    first = await client.post(f"/nodes/{node.id}/executions")
    assert first.status_code == 202
    assert first.json()["status"] == "queued"

    second = await client.post(f"/nodes/{node.id}/executions")
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "execution_in_flight"
    assert body["error"]["detail"]["execution_id"] == first.json()["id"]


async def test_unknown_node_type_rejected(client, branch):
    response = await client.post(
        f"/branches/{branch.id}/nodes", json={"type": "nonsense", "title": "X"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_node_type"


async def test_404_envelope(client):
    response = await client.get("/documents/00000000-0000-0000-0000-00000000beef")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_document_run_returns_created_and_skipped(client, document, branch, node):
    response = await client.post(f"/documents/{document.id}/run")
    assert response.status_code == 202
    body = response.json()
    assert len(body["created"]) == 1 and body["skipped"] == []


async def test_execution_history_keyset_pagination(client, session, node, enqueuer, blobstore):
    from app.spine.executions import enqueue_run
    from app.worker.tasks import execute_node

    for _ in range(3):
        execution = await enqueue_run(session, node.id, enqueuer)
        await execute_node(session, blobstore, execution.id)

    page_one = (await client.get(f"/nodes/{node.id}/executions?limit=2")).json()
    assert len(page_one["items"]) == 2 and page_one["next"]
    page_two = (
        await client.get(f"/nodes/{node.id}/executions?limit=2&after={page_one['next']}")
    ).json()
    assert len(page_two["items"]) == 1
    ids = [e["id"] for e in page_one["items"] + page_two["items"]]
    assert len(set(ids)) == 3  # no overlap, no gaps


async def test_promote_endpoint(client, session, node, enqueuer, blobstore):
    from app.spine.executions import enqueue_run
    from app.worker.tasks import execute_node

    execution = await enqueue_run(session, node.id, enqueuer)
    blocked = await client.post(f"/executions/{execution.id}/promote")
    assert blocked.status_code == 422  # in-flight: not promotable

    await execute_node(session, blobstore, execution.id)
    promoted = await client.post(f"/executions/{execution.id}/promote")
    assert promoted.status_code == 200
    assert promoted.json()["label"] == "milestone"


async def test_export_endpoint_txt_only(client, document):
    ok = await client.get(f"/documents/{document.id}/export?format=txt")
    assert ok.status_code == 200
    assert "RESEARCH REPORT:" in ok.text

    bad = await client.get(f"/documents/{document.id}/export?format=pdf")
    assert bad.status_code == 422  # §16: plain text only in the MVP


async def test_upload_stages_blob_and_enqueues(client, session, branch, enqueuer, blobstore):
    from app.spine.models import Node

    upload_node = Node(
        branch_id=branch.id, type="fake_snapshot", title="Upload", ordinal=1,
        parameters={}, declared_inputs={},
    )
    session.add(upload_node)
    await session.commit()

    response = await client.post(
        f"/nodes/{upload_node.id}/upload",
        files={"file": ("notes.txt", b"some research notes", "text/plain")},
    )
    assert response.status_code == 202
    (execution_id, resolved) = enqueuer.jobs[-1]
    assert resolved["filename"] == "notes.txt"
    assert await blobstore.get(resolved["staging_uri"]) == b"some research notes"
