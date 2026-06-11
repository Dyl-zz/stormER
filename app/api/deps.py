"""FastAPI dependencies. get_current_user is the future-auth swap point
(CLAUDE.md §14): every query path goes through it even though the MVP always
returns the single seeded user."""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.config import get_settings
from app.db import get_session
from app.spine.blobstore import BlobStore, LocalBlobStore
from app.spine.models import User

_blobstore: BlobStore | None = None


def get_blobstore() -> BlobStore:
    global _blobstore
    if _blobstore is None:
        _blobstore = LocalBlobStore(get_settings().blob_dir)
    return _blobstore


async def get_current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if user is None:
        raise ApiError(500, "no_seed_user", "no user row — run `python -m app.seed` first")
    return user
