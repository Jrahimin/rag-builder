"""Unit tests for RedisRateLimiter."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.platform.infra.rate_limit.redis_rate_limiter import RedisRateLimiter

pytestmark = pytest.mark.unit


@pytest.fixture
def redis() -> AsyncMock:
    mock = AsyncMock()
    mock.incr = AsyncMock(side_effect=[1, 2])
    mock.expire = AsyncMock()
    return mock


async def test_allows_first_request(redis: AsyncMock) -> None:
    limiter = RedisRateLimiter(redis, max_requests=1, window_seconds=60)
    result = await limiter.check(uuid.uuid4())
    assert result.allowed is True
    redis.expire.assert_awaited_once()


async def test_blocks_when_over_limit(redis: AsyncMock) -> None:
    redis.incr = AsyncMock(return_value=2)
    limiter = RedisRateLimiter(redis, max_requests=1, window_seconds=60)
    result = await limiter.check(uuid.uuid4())
    assert result.allowed is False
    assert result.retry_after_seconds >= 1
