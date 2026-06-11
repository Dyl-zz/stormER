"""ARQ-backed JobEnqueuer. The spine talks to the JobEnqueuer protocol; this is
the only place the API process touches ARQ."""

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings


class ArqEnqueuer:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue_run_node(
        self, execution_id: uuid.UUID, resolved_inputs: dict | None = None
    ) -> None:
        await self._pool.enqueue_job("run_node", str(execution_id), resolved_inputs or {})


_pool: ArqRedis | None = None


async def get_enqueuer() -> ArqEnqueuer:
    """FastAPI dependency. One shared redis pool per process."""
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return ArqEnqueuer(_pool)
