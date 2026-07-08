"""Organization business orchestration."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.organization import Organization
from app.modules.organizations.repositories.organization_repository import OrganizationRepository
from app.modules.organizations.schemas.organization import OrganizationCreate, OrganizationUpdate
from app.platform.auth.contracts import AuthEventPublisher
from app.platform.auth.events import OrganizationAuthInvalidated
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
    toggle_active_status,
)
from app.platform.domain.lifecycle_service import (
    soft_delete as soft_delete_entity,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult

_NOT_FOUND = {"message": "Organization not found.", "code": "organization_not_found"}
_DELETED = {"message": "Cannot modify a deleted organization.", "code": "organization_deleted"}


class OrganizationService:
    """Orchestrates Organization CRUD, status updates, and soft delete."""

    def __init__(
        self,
        session: AsyncSession,
        repository: OrganizationRepository,
        *,
        auth_events: AuthEventPublisher | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._auth_events = auth_events

    async def _publish_organization_auth_invalidated(self, organization_id: uuid.UUID) -> None:
        if self._auth_events is None:
            return
        await self._auth_events.publish(OrganizationAuthInvalidated(organization_id))

    async def create(self, data: OrganizationCreate) -> Organization:
        organization = Organization(
            name=data.name,
            description=data.description,
            is_active=True,
        )
        self._repository.add(organization)
        return await flush_commit_refresh(self._session, self._repository, organization)

    async def get(self, organization_id: uuid.UUID) -> Organization:
        return await get_or_raise(
            self._repository,
            organization_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
        )

    async def list(self, params: ListParams) -> PaginatedResult[Organization]:
        return await list_paginated(self._repository, params)

    async def update(self, organization_id: uuid.UUID, data: OrganizationUpdate) -> Organization:
        if not data.model_fields_set:
            raise BadRequestError(
                message="At least one field must be provided.",
                code="empty_update",
            )

        organization = await self._require_mutable(organization_id)

        if data.name is not None:
            organization.name = data.name
        if "description" in data.model_fields_set:
            organization.description = data.description

        return await flush_commit_refresh(self._session, self._repository, organization)

    async def toggle_status(self, organization_id: uuid.UUID) -> Organization:
        organization = await toggle_active_status(
            self._session,
            self._repository,
            organization_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
            deleted_message=_DELETED["message"],
            deleted_code=_DELETED["code"],
        )
        await self._publish_organization_auth_invalidated(organization_id)
        return organization

    async def soft_delete(self, organization_id: uuid.UUID) -> Organization:
        organization = await soft_delete_entity(
            self._session,
            self._repository,
            organization_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
        )
        await self._publish_organization_auth_invalidated(organization_id)
        return organization

    async def _require_mutable(self, organization_id: uuid.UUID) -> Organization:
        organization = await get_or_raise(
            self._repository,
            organization_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(organization, **_DELETED)
        return organization
