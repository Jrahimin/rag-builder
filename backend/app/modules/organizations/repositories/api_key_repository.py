"""Organization API key persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.organization import Organization
from app.models.organization_api_key import OrganizationApiKey
from app.platform.persistence.async_repository import AsyncRepository
from app.platform.persistence.filters import not_deleted_filter


class ApiKeyRepository(AsyncRepository[OrganizationApiKey]):
    """Async CRUD for Organization API keys."""

    model = OrganizationApiKey

    async def get_by_key_hash(self, key_hash: str) -> OrganizationApiKey | None:
        stmt = select(OrganizationApiKey).where(
            OrganizationApiKey.key_hash == key_hash,
            OrganizationApiKey.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_for_organization(
        self,
        key_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> OrganizationApiKey | None:
        stmt = select(OrganizationApiKey).where(
            OrganizationApiKey.id == key_id,
            OrganizationApiKey.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[OrganizationApiKey]:
        stmt = (
            select(OrganizationApiKey)
            .where(OrganizationApiKey.organization_id == organization_id)
            .order_by(OrganizationApiKey.created_at, OrganizationApiKey.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(OrganizationApiKey)
            .where(OrganizationApiKey.organization_id == organization_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def exists_active_name(
        self,
        organization_id: uuid.UUID,
        name: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        clauses = [
            OrganizationApiKey.organization_id == organization_id,
            OrganizationApiKey.name == name,
            OrganizationApiKey.revoked_at.is_(None),
        ]
        if exclude_id is not None:
            clauses.append(OrganizationApiKey.id != exclude_id)
        from sqlalchemy import func

        stmt = select(func.count()).select_from(OrganizationApiKey).where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0

    async def list_active_hashes_for_organization(
        self,
        organization_id: uuid.UUID,
    ) -> list[str]:
        stmt = select(OrganizationApiKey.key_hash).where(
            OrganizationApiKey.organization_id == organization_id,
            OrganizationApiKey.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def touch_last_used(self, api_key: OrganizationApiKey) -> None:
        api_key.last_used_at = datetime.now(UTC)
        await self._session.flush()

    async def get_organization_for_key(self, api_key: OrganizationApiKey) -> Organization | None:
        stmt = select(Organization).where(Organization.id == api_key.organization_id)
        if getattr(Organization, "deleted_at", None) is not None:
            stmt = stmt.where(not_deleted_filter(Organization))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
