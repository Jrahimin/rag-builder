"""Organization API key lifecycle — generate, hash, create, rotate, revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.organization import Organization
from app.models.organization_api_key import OrganizationApiKey
from app.modules.organizations.repositories.api_key_repository import ApiKeyRepository
from app.modules.organizations.repositories.organization_repository import OrganizationRepository
from app.modules.organizations.schemas.api_key import ApiKeyCreate
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.auth.contracts import AuthEventPublisher
from app.platform.auth.events import ApiKeyAuthInvalidated
from app.platform.domain.api_key_crypto import generate_key, hash_key, key_display_prefix
from app.platform.domain.lifecycle_service import get_or_raise
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import PaginatedResult

_NOT_FOUND = {"message": "API key not found.", "code": "api_key_not_found"}
_ORG_NOT_FOUND = {"message": "Organization not found.", "code": "organization_not_found"}
_NAME_CONFLICT = ConflictError(
    message="An active API key with this name already exists.",
    code="api_key_name_conflict",
)
_MAX_KEY_NAME_LEN = 64
_ROTATE_SUFFIX = "-rotated"


def _rotation_name(base_name: str, suffix_index: int = 0) -> str:
    """Build a rotation name that fits within the column length limit."""
    suffix = _ROTATE_SUFFIX if suffix_index == 0 else f"{_ROTATE_SUFFIX}-{suffix_index}"
    max_base_len = _MAX_KEY_NAME_LEN - len(suffix)
    return f"{base_name[:max_base_len]}{suffix}"


class ApiKeyService:
    """Manages named, rotatable Organization API keys."""

    def __init__(
        self,
        session: AsyncSession,
        api_key_repository: ApiKeyRepository,
        organization_repository: OrganizationRepository,
        *,
        key_pepper: str,
        auth_events: AuthEventPublisher | None = None,
        audit: AuditRecorder | None = None,
        actor_id: str | None = None,
    ) -> None:
        self._session = session
        self._api_key_repository = api_key_repository
        self._organization_repository = organization_repository
        self._key_pepper = key_pepper
        self._auth_events = auth_events
        self._audit = audit
        self._actor_id = actor_id

    async def _publish_api_key_auth_invalidated(self, key_hash: str) -> None:
        if self._auth_events is None:
            return
        await self._auth_events.publish(ApiKeyAuthInvalidated(key_hash))

    async def create(
        self,
        organization_id: uuid.UUID,
        data: ApiKeyCreate,
    ) -> tuple[OrganizationApiKey, str]:
        await self._require_active_organization(organization_id)
        if await self._api_key_repository.exists_active_name(organization_id, data.name):
            raise _NAME_CONFLICT

        raw_key = generate_key()
        api_key = OrganizationApiKey(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=data.name,
            key_prefix=key_display_prefix(raw_key),
            key_hash=hash_key(raw_key, self._key_pepper),
            created_by=self._actor_id,
        )
        self._api_key_repository.add(api_key)
        self._record(
            api_key,
            AuditEventType.API_KEY_CREATED,
            detail={"name": api_key.name, "key_prefix": api_key.key_prefix},
        )
        persisted = await flush_commit_refresh(
            self._session,
            self._api_key_repository,
            api_key,
            on_integrity=lambda: _NAME_CONFLICT,
        )
        return persisted, raw_key

    async def list(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> PaginatedResult[OrganizationApiKey]:
        await get_or_raise(
            self._organization_repository,
            organization_id,
            message=_ORG_NOT_FOUND["message"],
            code=_ORG_NOT_FOUND["code"],
            include_deleted=True,
        )
        items = await self._api_key_repository.list_for_organization(
            organization_id,
            limit=limit,
            offset=offset,
        )
        total = await self._api_key_repository.count_for_organization(organization_id)
        return PaginatedResult(items=items, total=total, limit=limit, offset=offset)

    async def rotate(
        self,
        organization_id: uuid.UUID,
        key_id: uuid.UUID,
        *,
        revoke_old: bool = False,
        replacement_name: str | None = None,
    ) -> tuple[OrganizationApiKey, str]:
        await self._require_active_organization(organization_id)
        old_key = await self._api_key_repository.get_by_id_for_organization(key_id, organization_id)
        if old_key is None or old_key.revoked_at is not None:
            raise NotFoundError(
                message=_NOT_FOUND["message"],
                code=_NOT_FOUND["code"],
            )

        if replacement_name is not None:
            new_name = replacement_name
            if await self._api_key_repository.exists_active_name(organization_id, new_name):
                raise _NAME_CONFLICT
        else:
            suffix_index = 0
            new_name = _rotation_name(old_key.name, suffix_index)
            while await self._api_key_repository.exists_active_name(organization_id, new_name):
                suffix_index += 1
                new_name = _rotation_name(old_key.name, suffix_index)

        raw_key = generate_key()
        new_key = OrganizationApiKey(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=new_name,
            key_prefix=key_display_prefix(raw_key),
            key_hash=hash_key(raw_key, self._key_pepper),
            created_by=self._actor_id,
            rotated_from_key_id=old_key.id,
        )
        self._api_key_repository.add(new_key)

        old_key_hash: str | None = None
        if revoke_old:
            old_key.revoked_at = datetime.now(UTC)
            old_key_hash = old_key.key_hash
        self._record(
            new_key,
            AuditEventType.API_KEY_ROTATED,
            detail={
                "replaces_key_id": str(old_key.id),
                "old_key_revoked": revoke_old,
                "key_prefix": new_key.key_prefix,
            },
        )

        persisted = await flush_commit_refresh(
            self._session,
            self._api_key_repository,
            new_key,
            on_integrity=lambda: _NAME_CONFLICT,
        )

        if old_key_hash is not None:
            await self._publish_api_key_auth_invalidated(old_key_hash)

        return persisted, raw_key

    async def revoke(self, organization_id: uuid.UUID, key_id: uuid.UUID) -> OrganizationApiKey:
        api_key = await self._api_key_repository.get_by_id_for_organization(key_id, organization_id)
        if api_key is None:
            raise NotFoundError(
                message=_NOT_FOUND["message"],
                code=_NOT_FOUND["code"],
            )

        key_hash: str | None = None
        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(UTC)
            key_hash = api_key.key_hash
            self._record(
                api_key,
                AuditEventType.API_KEY_REVOKED,
                detail={"key_prefix": api_key.key_prefix},
            )

        revoked = await flush_commit_refresh(self._session, self._api_key_repository, api_key)

        if key_hash is not None:
            await self._publish_api_key_auth_invalidated(key_hash)

        return revoked

    async def _require_active_organization(self, organization_id: uuid.UUID) -> Organization:
        organization = await get_or_raise(
            self._organization_repository,
            organization_id,
            message=_ORG_NOT_FOUND["message"],
            code=_ORG_NOT_FOUND["code"],
            include_deleted=True,
        )
        if isinstance(organization.deleted_at, datetime):
            raise ConflictError(
                message="Archived Organizations cannot issue or rotate API keys.",
                code="organization_archived",
            )
        if organization.is_active is False:
            raise ConflictError(
                message="Disabled Organizations cannot issue or rotate API keys.",
                code="organization_disabled",
            )
        return organization

    def _record(
        self,
        api_key: OrganizationApiKey,
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
            organization_id=api_key.organization_id,
            resource_type="organization_api_key",
            resource_id=api_key.id,
            outcome=AuditOutcome.SUCCESS,
            detail=detail,
        )
