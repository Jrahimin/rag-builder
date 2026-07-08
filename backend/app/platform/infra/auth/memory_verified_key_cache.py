"""In-process verified-key cache for dev and single-worker deployments."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.platform.auth.contracts import CachedVerifiedKey, VerifiedKeyCache


@dataclass(slots=True)
class _CacheEntry:
    value: CachedVerifiedKey
    expires_at: float


class MemoryVerifiedKeyCache:
    """Process-local TTL cache — not shared across workers."""

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._org_hashes: dict[uuid.UUID, set[str]] = {}

    async def get(self, key_hash: str) -> CachedVerifiedKey | None:
        entry = self._entries.get(key_hash)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            await self.invalidate(key_hash)
            return None
        return entry.value

    async def set(
        self,
        key_hash: str,
        value: CachedVerifiedKey,
        *,
        ttl_seconds: int,
    ) -> None:
        self._entries[key_hash] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + ttl_seconds,
        )
        self._org_hashes.setdefault(value.organization_id, set()).add(key_hash)

    async def invalidate(self, key_hash: str) -> None:
        entry = self._entries.pop(key_hash, None)
        if entry is not None:
            hashes = self._org_hashes.get(entry.value.organization_id)
            if hashes is not None:
                hashes.discard(key_hash)
                if not hashes:
                    self._org_hashes.pop(entry.value.organization_id, None)

    async def invalidate_organization(self, organization_id: uuid.UUID) -> None:
        hashes = list(self._org_hashes.pop(organization_id, set()))
        for key_hash in hashes:
            self._entries.pop(key_hash, None)


def get_memory_verified_key_cache() -> VerifiedKeyCache:
    """Return a module-level singleton memory cache."""
    return _CACHE


_CACHE = MemoryVerifiedKeyCache()
