"""End-to-end demo against a running API + worker.

Exercises the full analyst loop from CLAUDE.md §1/§17:
  1. create a document with two branches
  2. EDGAR node -> real 10-K risk-factors pull for Apple (CIK 320193)
  3. Upload/RAG node -> index a small file, then ask it a question
  4. promote the EDGAR run to milestone, cite it with a chip
  5. write the thesis, batch re-run the document (snapshot is skipped)
  6. export the report as plain text

Run:  python scripts/demo.py
Needs: API on :8000, ARQ worker running, Postgres + Redis up.
"""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


def check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        sys.exit(f"FAILED {response.request.method} {response.request.url}\n{response.text}")
    return response.json()


def wait_for(client: httpx.Client, execution_id: str, what: str, timeout_s: int = 240) -> dict:
    print(f"  ... waiting on {what} ({execution_id})", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        execution = check(client.get(f"{BASE}/executions/{execution_id}"))
        if execution["status"] in ("succeeded", "failed"):
            if execution["status"] == "failed":
                sys.exit(f"  {what} FAILED: {execution['error']}")
            print(f"  {what}: succeeded")
            return execution
        time.sleep(2)  # the §13 client poll
    sys.exit(f"  {what} timed out after {timeout_s}s")


def main() -> None:
    with httpx.Client(timeout=30) as client:
        print("1. Document + branches")
        doc = check(client.post(f"{BASE}/documents", json={"title": "Apple Inc — Demo Initiation"}))
        filings = check(
            client.post(
                f"{BASE}/documents/{doc['id']}/branches",
                json={"name": "Filings", "color": "#1f77b4"},
            )
        )
        notes = check(
            client.post(
                f"{BASE}/documents/{doc['id']}/branches",
                json={"name": "Field Notes", "color": "#2ca02c"},
            )
        )

        print("2. EDGAR node — live 10-K risk factors, Apple (CIK 320193)")
        edgar_node = check(
            client.post(
                f"{BASE}/branches/{filings['id']}/nodes",
                json={
                    "type": "edgar",
                    "title": "Risk factors (10-K)",
                    "parameters": {"cik": "320193", "form_type": "10-K", "section": "risk_factors"},
                },
            )
        )
        edgar_run = check(client.post(f"{BASE}/nodes/{edgar_node['id']}/executions"))
        edgar_execution = wait_for(client, edgar_run["id"], "EDGAR pull")
        print(f"  finding starts: {edgar_execution['generated_text'][:120]!r}")

        print("3. Upload/RAG node — index a file, then question it")
        rag_node = check(
            client.post(
                f"{BASE}/branches/{notes['id']}/nodes",
                json={"type": "upload_rag", "title": "Expert call notes", "parameters": {}},
            )
        )
        upload = check(
            client.post(
                f"{BASE}/nodes/{rag_node['id']}/upload",
                files={
                    "file": (
                        "expert_notes.txt",
                        b"The supply chain contact says iPhone demand in China is softening.\n\n"
                        b"Services margin is structurally higher than hardware and still expanding.\n\n"
                        b"Vision Pro volumes remain immaterial to revenue through 2026.",
                        "text/plain",
                    )
                },
            )
        )
        wait_for(client, upload["id"], "RAG indexing")
        check(
            client.patch(
                f"{BASE}/nodes/{rag_node['id']}",
                json={"parameters": {"question": "what did the expert say about services margin?"}},
            )
        )
        question_run = check(client.post(f"{BASE}/nodes/{rag_node['id']}/executions"))
        wait_for(client, question_run["id"], "RAG question")

        print("4. Promote the EDGAR run to milestone, cite it")
        check(client.post(f"{BASE}/executions/{edgar_execution['id']}/promote"))
        chip = check(
            client.post(
                f"{BASE}/documents/{doc['id']}/chips",
                json={"node_id": edgar_node["id"], "execution_id": edgar_execution["id"]},
            )
        )

        print("5. Thesis + document-level re-run")
        check(
            client.put(
                f"{BASE}/documents/{doc['id']}/center",
                json={
                    "markdown": "Apple's moat is intact, but the 10-K's own risk language "
                    f"on supply concentration deserves weight {chip['token']}."
                },
            )
        )
        rerun = check(client.post(f"{BASE}/documents/{doc['id']}/run"))
        print(f"  re-run created {len(rerun['created'])}, skipped {rerun['skipped']}")
        for execution_id in rerun["created"]:
            wait_for(client, execution_id, "re-run node")

        print("6. Export\n")
        export = client.get(f"{BASE}/documents/{doc['id']}/export?format=txt")
        print(export.text)


if __name__ == "__main__":
    main()
