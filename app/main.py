"""FastAPI app factory. Run with: uvicorn app.main:app"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import get_blobstore
from app.api.errors import install_handlers
from app.api.routes import documents, executions, frameworks, nodes
from app.config import get_settings
from app.db import get_session_factory
from app.runners import register_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    register_all(get_settings(), get_session_factory(), get_blobstore())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Modular Research Canvas", lifespan=lifespan)
    install_handlers(app)
    for router in (documents.router, nodes.router, executions.router, frameworks.router):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
