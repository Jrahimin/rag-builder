"""Local filesystem object storage — default for development and tests."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
import aiofiles.os

from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError


class LocalFilesystemStorageProvider(BaseStorageProvider):
    """Stores objects under a configurable root directory on disk."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path_for(self, key: str) -> Path:
        normalized = key.lstrip("/").replace("\\", "/")
        path = (self._root / normalized).resolve()
        if not path.is_relative_to(self._root):
            msg = "Storage key escapes configured root"
            raise ProviderError(msg, provider_name="local")
        return path

    async def put(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        content_type: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        del content_type, size_bytes
        path = self._path_for(key)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        async with aiofiles.open(path, "wb") as handle:
            async for chunk in stream:
                await handle.write(chunk)

    async def get(self, key: str) -> AsyncIterator[bytes]:
        path = self._path_for(key)
        if not path.is_file():
            msg = f"Object not found: {key}"
            raise ProviderError(msg, provider_name="local")

        async with aiofiles.open(path, "rb") as handle:
            while True:
                chunk = await handle.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._path_for(key)
        if path.is_file():
            await aiofiles.os.remove(path)

    async def check(self) -> None:
        """Ensure the local storage root can be created and inspected."""
        await aiofiles.os.makedirs(self._root, exist_ok=True)
        if not self._root.is_dir():
            msg = "Configured local storage root is not a directory"
            raise ProviderError(msg, provider_name="local")

    async def list_keys(self, prefix: str) -> list[str]:
        base = self._path_for(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        paths = await asyncio.to_thread(
            lambda: sorted(path for path in base.rglob("*") if path.is_file())
        )
        return [path.relative_to(self._root).as_posix() for path in paths]

    async def delete_document_tree(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        document_dir = self._path_for(f"{project_id}/{document_id}")
        if not document_dir.exists():
            return
        await asyncio.to_thread(shutil.rmtree, document_dir, True)
