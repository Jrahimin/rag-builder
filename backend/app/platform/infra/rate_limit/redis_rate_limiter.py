"""Redis fixed-window rate limiter keyed by organization."""

from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis

from app.platform.rate_limit.contracts import RateLimitResult

_KEY_PREFIX = "ape:ratelimit:org:"


class RedisRateLimiter:
    """Fixed-window counter rate limiter using Redis INCR + EXPIRE."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_requests: int,
        window_seconds: int,
    ) -> None:
        self._redis = redis
        self._max_requests = max_requests
        self._window_seconds = window_seconds

    async def check(self, organization_id: uuid.UUID) -> RateLimitResult:
        now = int(time.time())
        window_bucket = now // self._window_seconds
        key = f"{_KEY_PREFIX}{organization_id}:{window_bucket}"

        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, self._window_seconds)

        if count > self._max_requests:
            elapsed_in_window = now % self._window_seconds
            retry_after = self._window_seconds - elapsed_in_window
            return RateLimitResult(allowed=False, retry_after_seconds=max(retry_after, 1))

        return RateLimitResult(allowed=True)
