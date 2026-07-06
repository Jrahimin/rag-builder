"""Vector store provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, VectorStoreBackend, get_settings
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider
from app.platform.providers.implementations.memory_vector_store import MemoryVectorStoreProvider
from app.platform.providers.implementations.qdrant_vector_store_provider import (
    QdrantVectorStoreProvider,
)

_memory_store: MemoryVectorStoreProvider | None = None


def create_vector_store_provider(settings: Settings) -> BaseVectorStoreProvider:
    """Build the configured vector store provider."""
    global _memory_store
    if settings.vector_store.backend is VectorStoreBackend.MEMORY:
        if _memory_store is None:
            _memory_store = MemoryVectorStoreProvider()
        return _memory_store
    return QdrantVectorStoreProvider(
        qdrant=settings.qdrant,
        vector_store=settings.vector_store,
    )


@lru_cache
def get_vector_store_provider() -> BaseVectorStoreProvider:
    """Return the process-scoped vector store provider."""
    return create_vector_store_provider(get_settings())
