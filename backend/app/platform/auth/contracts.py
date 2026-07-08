"""Authentication infrastructure contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.platform.auth.events import AuthDomainEvent


@dataclass(frozen=True, slots=True)
class CachedVerifiedKey:
    """Minimal fields cached after successful key verification."""

    organization_id: uuid.UUID
    organization_is_active: bool
    api_key_id: uuid.UUID


class VerifiedKeyCache(Protocol):
    """Short-lived positive cache for verified API keys."""

    async def get(self, key_hash: str) -> CachedVerifiedKey | None: ...

    async def set(
        self,
        key_hash: str,
        value: CachedVerifiedKey,
        *,
        ttl_seconds: int,
    ) -> None: ...

    async def invalidate(self, key_hash: str) -> None: ...

    async def invalidate_organization(self, organization_id: uuid.UUID) -> None: ...


class AuthEventPublisher(Protocol):
    """Publishes auth invalidation events after successful credential mutations."""

    async def publish(self, event: AuthDomainEvent) -> None: ...
