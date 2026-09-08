"""Deployment-wide recovery capacity using the existing Redis service."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.platform.providers.errors import ProviderError

_ACQUIRE = """
local now = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""


@asynccontextmanager
async def recovery_slot(redis_dsn: str, *, limit: int = 12) -> AsyncIterator[None]:
    """A crash-expiring lease; lifetime exceeds the enclosing 120-second repair deadline."""
    token = uuid.uuid4().hex
    key = "ape:rag:recovery-capacity:v1"
    async with Redis.from_url(redis_dsn, socket_connect_timeout=3, socket_timeout=3) as redis:
        try:
            while True:
                # Redis 3 (supported by the local Windows setup) forbids TIME followed
                # by writes inside Lua. Read server time first; admission remains atomic.
                seconds, micros = await redis.time()
                now = seconds * 1000 + micros // 1000
                acquired = await cast(
                    Awaitable[Any], redis.eval(_ACQUIRE, 1, key, limit, 180_000, token, now)
                )
                if acquired:
                    break
                await asyncio.sleep(0.1)
        except RedisError as exc:
            raise ProviderError(
                "Recovery capacity unavailable",
                provider_name="retrieval",
                context={"reason": "capacity_unavailable"},
            ) from exc
        try:
            yield
        finally:
            # Expiry is the backstop after process/connection failure.
            with suppress(RedisError):
                await redis.zrem(key, token)
