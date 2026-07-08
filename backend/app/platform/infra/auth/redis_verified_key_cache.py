"""Redis-backed verified-key cache shared across API workers."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from app.platform.auth.contracts import CachedVerifiedKey

_KEY_PREFIX = "ape:auth:verified:"
_ORG_SET_PREFIX = "ape:auth:org_keys:"


class RedisVerifiedKeyCache:
    """Short-lived positive cache stored in Redis."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key_hash: str) -> CachedVerifiedKey | None:
        raw = await self._redis.get(f"{_KEY_PREFIX}{key_hash}")
        if raw is None:
            return None
        data = json.loads(raw)
        return CachedVerifiedKey(
            organization_id=uuid.UUID(data["organization_id"]),
            organization_is_active=data["organization_is_active"],
            api_key_id=uuid.UUID(data["api_key_id"]),
        )

    async def set(
        self,
        key_hash: str,
        value: CachedVerifiedKey,
        *,
        ttl_seconds: int,
    ) -> None:
        cache_key = f"{_KEY_PREFIX}{key_hash}"
        payload = json.dumps(
            {
                "organization_id": str(value.organization_id),
                "organization_is_active": value.organization_is_active,
                "api_key_id": str(value.api_key_id),
            }
        )
        org_set_key = f"{_ORG_SET_PREFIX}{value.organization_id}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(cache_key, payload, ex=ttl_seconds)
            pipe.sadd(org_set_key, key_hash)
            pipe.expire(org_set_key, ttl_seconds)
            await pipe.execute()

    async def invalidate(self, key_hash: str) -> None:
        cache_key = f"{_KEY_PREFIX}{key_hash}"
        raw = await self._redis.get(cache_key)
        if raw is not None:
            data = json.loads(raw)
            org_id = data["organization_id"]
            org_set_key = f"{_ORG_SET_PREFIX}{org_id}"
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(cache_key)
                pipe.srem(org_set_key, key_hash)
                await pipe.execute()
            return
        await self._redis.delete(cache_key)

    async def invalidate_organization(self, organization_id: uuid.UUID) -> None:
        org_set_key = f"{_ORG_SET_PREFIX}{organization_id}"
        key_hashes = await cast(Awaitable[set[str]], self._redis.smembers(org_set_key))
        if not key_hashes:
            return
        async with self._redis.pipeline(transaction=True) as pipe:
            for key_hash in key_hashes:
                pipe.delete(f"{_KEY_PREFIX}{key_hash}")
            pipe.delete(org_set_key)
            await pipe.execute()
