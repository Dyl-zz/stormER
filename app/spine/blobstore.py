"""BlobStore interface (CLAUDE.md §4, §6). Raw bytes live here, never in Postgres.

Worker rule: the blob is written FIRST, then the node_execution row pointing at
it. An orphan blob is recoverable; a row pointing at a missing blob must not happen.
"""

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path


class BlobNotFound(Exception):
    pass


class BlobStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, meta: dict) -> str:
        """Write a blob. Returns the URI to store on the execution row."""

    @abstractmethod
    async def get(self, uri: str) -> bytes:
        """Read a blob by the URI returned from put(). Raises BlobNotFound."""

    @abstractmethod
    async def delete(self, uri: str) -> None:
        """Remove a blob (the retention sweep). Idempotent."""


def blob_meta(data: bytes, mime_type: str) -> dict:
    """Standard raw_data_meta contents: content hash, byte size, MIME type (§6)."""
    return {
        "content_hash": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "mime_type": mime_type,
    }


class LocalBlobStore(BlobStore):
    """Filesystem store for the single-analyst MVP deployment and tests.
    S3-compatible storage is a drop-in replacement behind the same interface."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, uri: str) -> Path:
        if not uri.startswith("local://"):
            raise ValueError(f"not a local blob uri: {uri}")
        relative = uri.removeprefix("local://")
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"blob uri escapes store root: {uri}")
        return path

    async def put(self, key: str, data: bytes, meta: dict) -> str:
        uri = f"local://{key}"
        path = self._path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return uri

    async def get(self, uri: str) -> bytes:
        path = self._path(uri)
        if not path.exists():
            raise BlobNotFound(uri)
        return path.read_bytes()

    async def delete(self, uri: str) -> None:
        self._path(uri).unlink(missing_ok=True)
