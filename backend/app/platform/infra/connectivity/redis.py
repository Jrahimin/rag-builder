"""Redis connectivity adapter for health checks and future queue wiring.

Not a business provider. The ``redis`` SDK is used only inside this module.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class RedisConnectivity:
    """Owns a single async Redis connection pool for infrastructure use."""

    def __init__(self, settings: Settings) -> None:
        self._client: Redis = Redis.from_url(
            settings.redis.dsn,
            encoding="utf-8",
            decode_responses=True,
        )

    @property
    def client(self) -> Redis:
        """Expose the underlying async Redis client for auth and rate limiting."""
        return self._client

    async def check(self) -> None:
        """Ping Redis to verify connectivity (raises on failure)."""
        await cast(Awaitable[bool], self._client.ping())

    async def dispose(self) -> None:
        """Close the connection pool on shutdown."""
        await self._client.aclose()
        log.info("redis_disposed")
