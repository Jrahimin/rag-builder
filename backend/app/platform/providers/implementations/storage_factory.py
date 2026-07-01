"""Factory for the configured object storage provider."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, StorageBackend, get_settings
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.local_storage import LocalFilesystemStorageProvider
from app.platform.providers.implementations.minio_storage import MinioStorageProvider


@lru_cache
def get_storage_provider() -> BaseStorageProvider:
    """Return a process-scoped storage provider from application settings."""
    settings = get_settings()
    return create_storage_provider(settings)


def create_storage_provider(settings: Settings) -> BaseStorageProvider:
    """Build a storage provider for the given settings (uncached; tests)."""
    backend = settings.storage.backend
    if backend == StorageBackend.LOCAL:
        return LocalFilesystemStorageProvider(settings.storage.local_root)
    if backend == StorageBackend.MINIO:
        return MinioStorageProvider(settings.minio)
    msg = f"Unsupported storage backend: {backend!r}"
    raise ProviderError(msg, provider_name="storage_factory")
