"""Object storage provider contract — stream-friendly, vendor-agnostic."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import BinaryIO


class BaseStorageProvider(ABC):
    """Persist and retrieve binary artifacts by opaque storage key.

    Implementations must not leak SDK types. Keys are path-like strings
    (e.g. ``{project_id}/{document_id}/{filename}``) with no leading slash.
    """

    @abstractmethod
    async def put(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        content_type: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        """Store object bytes from an async byte stream."""

    @abstractmethod
    def get(self, key: str) -> AsyncIterator[bytes]:
        """Yield object bytes for ``key``; raise when missing."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove object at ``key``; idempotent when already absent."""

    async def delete_document_tree(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """Remove provider-native containers for ``{project_id}/{document_id}`` after object deletes."""
        del project_id, document_id

    async def get_download_url(self, key: str, *, expires_seconds: int = 3600) -> str | None:
        """Optional presigned URL for direct client download (MinIO/S3)."""
        return None

    @staticmethod
    async def iter_file(file: BinaryIO, *, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        """Adapt a synchronous readable file object to an async byte stream."""
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            yield chunk
