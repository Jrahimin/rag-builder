"""Async Qdrant client lifecycle.

This module manages *connectivity only*. Vector operations must be performed
through a ``BaseVectorStoreProvider`` abstraction (added in a later sprint) so
the application core stays vector-database agnostic and Qdrant can be swapped
for pgvector / Milvus / Pinecone without touching business logic.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class QdrantConnection:
    """Owns a single async Qdrant client for health checks and providers."""

    def __init__(self, settings: Settings) -> None:
        qdrant = settings.qdrant
        self._client: AsyncQdrantClient = AsyncQdrantClient(
            host=qdrant.host,
            port=qdrant.port,
            grpc_port=qdrant.grpc_port,
            prefer_grpc=qdrant.prefer_grpc,
            https=qdrant.https,
            api_key=qdrant.api_key,
            # The app/provider layer owns compatibility expectations; skip the
            # client's implicit server-version probe (also avoids noisy warnings).
            check_compatibility=False,
        )

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client

    async def check(self) -> None:
        """List collections to verify reachability (raises on failure)."""
        await self._client.get_collections()

    async def dispose(self) -> None:
        """Close the underlying client on shutdown."""
        await self._client.close()
        log.info("qdrant_disposed")
