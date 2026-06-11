"""The equity vertical (CLAUDE.md §5): every equity-specific concept lives in
these runners and their parameter/input-key models. The spine never imports
this package; processes call register_all() at startup."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.spine.blobstore import BlobStore
from app.spine.runner import register_runner


def register_all(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    blobstore: BlobStore,
) -> None:
    from app.runners.edgar import EdgarRunner
    from app.runners.upload_rag import UploadRagRunner
    from app.runners.websearch import WebSearchRunner

    register_runner(EdgarRunner(settings))
    register_runner(UploadRagRunner(settings, session_factory, blobstore))
    register_runner(WebSearchRunner(settings))
