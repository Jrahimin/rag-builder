"""Unit tests for the in-memory verified-key cache."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.platform.auth.contracts import CachedVerifiedKey
from app.platform.infra.auth.memory_verified_key_cache import MemoryVerifiedKeyCache

pytestmark = pytest.mark.unit


@pytest.fixture
def cache() -> MemoryVerifiedKeyCache:
    return MemoryVerifiedKeyCache()


async def test_cache_miss_returns_none(cache: MemoryVerifiedKeyCache) -> None:
    assert await cache.get("missing") is None


async def test_cache_hit_returns_value(cache: MemoryVerifiedKeyCache) -> None:
    org_id = uuid.uuid4()
    key_id = uuid.uuid4()
    value = CachedVerifiedKey(
        organization_id=org_id,
        organization_is_active=True,
        api_key_id=key_id,
    )
    await cache.set("hash1", value, ttl_seconds=60)
    cached = await cache.get("hash1")
    assert cached is not None
    assert cached.organization_id == org_id
    assert cached.api_key_id == key_id


async def test_cache_expires_after_ttl(cache: MemoryVerifiedKeyCache) -> None:
    value = CachedVerifiedKey(
        organization_id=uuid.uuid4(),
        organization_is_active=True,
        api_key_id=uuid.uuid4(),
    )
    await cache.set("hash2", value, ttl_seconds=1)
    await asyncio.sleep(1.1)
    assert await cache.get("hash2") is None


async def test_invalidate_removes_entry(cache: MemoryVerifiedKeyCache) -> None:
    value = CachedVerifiedKey(
        organization_id=uuid.uuid4(),
        organization_is_active=True,
        api_key_id=uuid.uuid4(),
    )
    await cache.set("hash3", value, ttl_seconds=60)
    await cache.invalidate("hash3")
    assert await cache.get("hash3") is None


async def test_invalidate_organization_removes_all_keys(cache: MemoryVerifiedKeyCache) -> None:
    org_id = uuid.uuid4()
    value = CachedVerifiedKey(
        organization_id=org_id,
        organization_is_active=True,
        api_key_id=uuid.uuid4(),
    )
    await cache.set("hash4", value, ttl_seconds=60)
    await cache.set("hash5", value, ttl_seconds=60)
    await cache.invalidate_organization(org_id)
    assert await cache.get("hash4") is None
    assert await cache.get("hash5") is None
