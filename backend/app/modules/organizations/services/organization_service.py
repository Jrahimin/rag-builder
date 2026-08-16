"""Organization business orchestration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.organization import Organization
from app.modules.organizations.repositories.organization_repository import OrganizationRepository
from app.modules.organizations.schemas.organization import OrganizationCreate, OrganizationUpdate
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.auth.contracts import AuthEventPublisher
from app.platform.auth.events import OrganizationAuthInvalidated
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.persistence.lifecycle import is_soft_deleted, mark_soft_deleted

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
        audit: AuditRecorder | None = None,
        actor_id: str | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._auth_events = auth_events
        self._audit = audit
        self._actor_id = actor_id

    async def _publish_organization_auth_invalidated(self, organization_id: uuid.UUID) -> None:
        if self._auth_events is None:
            return
        await self._auth_events.publish(OrganizationAuthInvalidated(organization_id))

    async def create(self, data: OrganizationCreate) -> Organization:
        organization = Organization(
            id=uuid.uuid4(),
            name=data.name,
            description=data.description,
            is_active=True,
        )
        self._repository.add(organization)
        if self._audit is not None:
            await self._repository.flush()
        self._record(
            organization,
            AuditEventType.ORGANIZATION_CREATED,
            detail={"name": organization.name},
        )
        return await flush_commit_refresh(self._session, self._repository, organization)

    async def get(
        self, organization_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Organization:
        return await get_or_raise(
            self._repository,
            organization_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=include_deleted,
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
        self._record(
            organization,
            AuditEventType.ORGANIZATION_UPDATED,
            detail={"fields": sorted(data.model_fields_set)},
        )
        return await flush_commit_refresh(self._session, self._repository, organization)

    async def set_status(self, organization_id: uuid.UUID, *, is_active: bool) -> Organization:
        organization = await self._require_mutable(organization_id)
        if organization.is_active == is_active:
            return organization
        organization.is_active = is_active
        self._record(
            organization,
            AuditEventType.ORGANIZATION_STATUS_CHANGED,
            detail={"is_active": is_active},
        )
        await self._repository.flush()
        await self._session.commit()
        await self._session.refresh(organization)
        await self._publish_organization_auth_invalidated(organization_id)
        return organization

    async def toggle_status(self, organization_id: uuid.UUID) -> Organization:
        organization = await self._require_mutable(organization_id)
        return await self.set_status(
            organization_id,
            is_active=not organization.is_active,
        )

    async def soft_delete(self, organization_id: uuid.UUID) -> Organization:
        organization = await get_or_raise(
            self._repository,
            organization_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        if is_soft_deleted(organization):
            return organization
        mark_soft_deleted(organization)
        self._record(
            organization,
            AuditEventType.ORGANIZATION_ARCHIVED,
            detail={"archived": True},
        )
        organization = await flush_commit_refresh(
            self._session,
            self._repository,
            organization,
        )
        await self._publish_organization_auth_invalidated(organization_id)
        return organization

    async def restore(self, organization_id: uuid.UUID) -> Organization:
        organization = await get_or_raise(
            self._repository,
            organization_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        if organization.deleted_at is None:
            return organization
        organization.deleted_at = None
        organization.deleted_by = None
        organization.is_active = False
        self._record(
            organization,
            AuditEventType.ORGANIZATION_RESTORED,
            detail={"restored_at": datetime.now(UTC).isoformat(), "is_active": False},
        )
        await self._repository.flush()
        await self._session.commit()
        await self._session.refresh(organization)
        await self._publish_organization_auth_invalidated(organization_id)
        return organization

    def _record(
        self,
        organization: Organization,
        event_type: AuditEventType,
        *,
        detail: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            actor_id=self._actor_id,
            organization_id=organization.id,
            resource_type="organization",
            resource_id=organization.id,
            outcome=AuditOutcome.SUCCESS,
            detail=detail,
        )

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
