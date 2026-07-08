"""Centralized verified-key cache invalidation for auth domain events."""

from __future__ import annotations

from app.platform.auth.contracts import VerifiedKeyCache
from app.platform.auth.events import (
    ApiKeyAuthInvalidated,
    AuthDomainEvent,
    OrganizationAuthInvalidated,
)


class VerifiedKeyCacheEventHandler:
    """Invalidate verified-key cache entries in response to auth domain events."""

    def __init__(self, cache: VerifiedKeyCache | None) -> None:
        self._cache = cache

    async def publish(self, event: AuthDomainEvent) -> None:
        if self._cache is None:
            return

        match event:
            case OrganizationAuthInvalidated(organization_id=organization_id):
                await self._cache.invalidate_organization(organization_id)
            case ApiKeyAuthInvalidated(key_hash=key_hash):
                await self._cache.invalidate(key_hash)
