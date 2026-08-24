"""Create and manage platform operator accounts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError
from app.models.admin_user import AdminRole, AdminUser
from app.modules.admin_auth.repositories.admin_user_repository import AdminUserRepository
from app.modules.admin_auth.repository import AdminAuthRepository
from app.modules.admin_auth.schemas import AdminUserCreate
from app.modules.admin_auth.security import hash_password
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.domain.lifecycle_service import get_or_raise, list_paginated, require_not_deleted
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.persistence.lifecycle import is_soft_deleted, mark_soft_deleted

_NOT_FOUND = {"message": "Admin user not found.", "code": "admin_user_not_found"}
_DELETED = {"message": "Cannot modify a deleted admin user.", "code": "admin_user_deleted"}
_EMAIL_CONFLICT = ConflictError(
    message="An admin with this email already exists.",
    code="admin_email_exists",
)
_PROTECTED = ForbiddenError(
    message="The bootstrap Super Admin cannot be changed from the console.",
    code="super_admin_protected",
)


class AdminUserService:
    def __init__(
        self,
        session: AsyncSession,
        repository: AdminUserRepository,
        auth_repository: AdminAuthRepository,
        *,
        actor_id: uuid.UUID,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._auth_repository = auth_repository
        self._actor_id = actor_id
        self._audit = audit

    async def create(self, data: AdminUserCreate) -> AdminUser:
        if await self._repository.exists_by_field("email", data.email):
            raise _EMAIL_CONFLICT
        admin = AdminUser(
            id=uuid.uuid4(),
            email=data.email,
            password_hash=hash_password(data.password),
            role=AdminRole.ADMIN.value,
            is_active=True,
        )
        self._repository.add(admin)
        if self._audit is not None:
            await self._repository.flush()
        self._record(admin, AuditEventType.ADMIN_USER_CREATED, detail={"email": admin.email})
        return await flush_commit_refresh(
            self._session,
            self._repository,
            admin,
            on_integrity=lambda: _EMAIL_CONFLICT,
        )

    async def get(self, admin_id: uuid.UUID, *, include_deleted: bool = False) -> AdminUser:
        return await get_or_raise(
            self._repository,
            admin_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=include_deleted,
        )

    async def list(self, params: ListParams) -> PaginatedResult[AdminUser]:
        return await list_paginated(self._repository, params)

    async def set_status(self, admin_id: uuid.UUID, *, is_active: bool) -> AdminUser:
        admin = await self._require_mutable(admin_id)
        if admin.is_active == is_active:
            return admin
        admin.is_active = is_active
        if not is_active:
            await self._auth_repository.revoke_sessions_for_admin(admin.id)
        self._record(
            admin,
            AuditEventType.ADMIN_USER_STATUS_CHANGED,
            detail={"is_active": is_active},
        )
        return await flush_commit_refresh(self._session, self._repository, admin)

    async def soft_delete(self, admin_id: uuid.UUID) -> AdminUser:
        admin = await get_or_raise(
            self._repository,
            admin_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        if is_soft_deleted(admin):
            return admin
        self._reject_protected_or_self(admin)
        mark_soft_deleted(admin, deleted_by=self._actor_id)
        await self._auth_repository.revoke_sessions_for_admin(admin.id)
        self._record(admin, AuditEventType.ADMIN_USER_ARCHIVED, detail={"archived": True})
        return await flush_commit_refresh(self._session, self._repository, admin)

    async def restore(self, admin_id: uuid.UUID) -> AdminUser:
        admin = await get_or_raise(
            self._repository,
            admin_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        if admin.deleted_at is None:
            return admin
        if admin.role == AdminRole.SUPER_ADMIN.value:
            raise _PROTECTED
        if await self._repository.exists_by_field("email", admin.email, exclude_id=admin.id):
            raise _EMAIL_CONFLICT
        admin.deleted_at = None
        admin.deleted_by = None
        admin.is_active = False
        self._record(
            admin,
            AuditEventType.ADMIN_USER_RESTORED,
            detail={"restored_at": datetime.now(UTC).isoformat(), "is_active": False},
        )
        return await flush_commit_refresh(self._session, self._repository, admin)

    async def _require_mutable(self, admin_id: uuid.UUID) -> AdminUser:
        admin = await get_or_raise(
            self._repository,
            admin_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(admin, **_DELETED)
        self._reject_protected_or_self(admin)
        return admin

    def _reject_protected_or_self(self, admin: AdminUser) -> None:
        if admin.role == AdminRole.SUPER_ADMIN.value:
            raise _PROTECTED
        if admin.id == self._actor_id:
            raise ForbiddenError(
                message="You cannot change your own account.",
                code="admin_self_mutation",
            )

    def _record(
        self,
        admin: AdminUser,
        event_type: AuditEventType,
        *,
        detail: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            actor_id=str(self._actor_id),
            resource_type="admin_user",
            resource_id=admin.id,
            outcome=AuditOutcome.SUCCESS,
            detail=detail,
        )
