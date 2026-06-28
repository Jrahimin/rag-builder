"""Async Redis client lifecycle.

Redis backs caching and the background job queue. As with the database, the
client is created at startup and closed at shutdown; handlers access it via
dependency injection.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class RedisClient:
    """Thin wrapper owning a single async Redis connection pool."""

    def __init__(self, settings: Settings) -> None:
        self._client: Redis = Redis.from_url(
            settings.redis.dsn,
            encoding="utf-8",
            decode_responses=True,
        )

    @property
    def client(self) -> Redis:
        return self._client

    async def check(self) -> None:
        """Ping Redis to verify connectivity (raises on failure)."""
        await self._client.ping()

    async def dispose(self) -> None:
        """Close the connection pool on shutdown."""
        await self._client.aclose()
        log.info("redis_disposed")
