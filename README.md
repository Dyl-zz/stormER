# Modular Research Canvas — MVP backend

Node-based equity research tool. Architecture and locked decisions: `CLAUDE.md`.
Symbol index: `CODEMAP.md`.

## Run

Requires PostgreSQL (with the `pgvector` extension available) and Redis.

```bash
pip install -e ".[dev]"

# configure (all settings prefixed RC_, see app/config.py)
export RC_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/research_canvas
export RC_REDIS_URL=redis://localhost:6379/0
export RC_ANTHROPIC_API_KEY=...   # only needed for the web-search node

alembic upgrade head      # schema
python -m app.seed        # the single MVP user
arq app.worker.tasks.WorkerSettings   # worker (terminal 1)
uvicorn app.main:app                  # API    (terminal 2)
```

## Test

```bash
pytest
```

Tests run on in-memory SQLite via SQLAlchemy type variants — same models, no
Postgres needed. The export renderer is locked by `tests/golden/export_basic.txt`;
intentional format changes regenerate it in the same commit.
# stormER
# stormER
# stormER
