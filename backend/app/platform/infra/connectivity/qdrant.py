"""Qdrant connectivity adapter for health checks.

Vector **operations** belong in ``platform/providers/implementations/`` behind
``BaseVectorStoreProvider``. This module only verifies reachability.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class QdrantConnectivity:
    """Owns a single async Qdrant client for infrastructure health checks."""

    def __init__(self, settings: Settings) -> None:
        qdrant = settings.qdrant
        self._client: AsyncQdrantClient = AsyncQdrantClient(
            host=qdrant.host,
            port=qdrant.port,
            grpc_port=qdrant.grpc_port,
            prefer_grpc=qdrant.prefer_grpc,
            https=qdrant.https,
            api_key=qdrant.api_key,
            check_compatibility=False,
        )

    async def check(self) -> None:
        """List collections to verify reachability (raises on failure)."""
        await self._client.get_collections()

    async def dispose(self) -> None:
        """Close the client on shutdown."""
        await self._client.close()
        log.info("qdrant_disposed")
