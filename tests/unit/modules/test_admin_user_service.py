"""Unit tests for AdminUserService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.admin_user import AdminRole, AdminUser
from app.modules.admin_auth.schemas import AdminUserCreate
from app.modules.admin_auth.services.admin_user_service import AdminUserService

pytestmark = pytest.mark.unit

_ACTOR_ID = uuid.uuid4()


def _admin(
    *,
    role: AdminRole = AdminRole.ADMIN,
    is_active: bool = True,
    email: str = "admin@example.com",
    admin_id: uuid.UUID | None = None,
) -> AdminUser:
    return AdminUser(
        id=admin_id or uuid.uuid4(),
        email=email,
        password_hash="hashed",
        role=role.value,
        is_active=is_active,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    mock.flush = AsyncMock()
    mock.exists_by_field = AsyncMock(return_value=False)
    return mock


@pytest.fixture
def auth_repository() -> AsyncMock:
    mock = AsyncMock()
    mock.revoke_sessions_for_admin = AsyncMock()
    return mock


@pytest.fixture
def service(
    session: AsyncMock, repository: AsyncMock, auth_repository: AsyncMock
) -> AdminUserService:
    return AdminUserService(session, repository, auth_repository, actor_id=_ACTOR_ID)


async def test_create_assigns_admin_role(
    service: AdminUserService, repository: AsyncMock, session: AsyncMock
) -> None:
    created = await service.create(AdminUserCreate(email="ops@example.com", password="password"))
    assert created.role == AdminRole.ADMIN.value
    assert created.email == "ops@example.com"
    assert created.id is not None
    repository.add.assert_called_once()
    session.commit.assert_awaited_once()


async def test_create_rejects_duplicate_email(
    service: AdminUserService, repository: AsyncMock
) -> None:
    repository.exists_by_field.return_value = True
    with pytest.raises(ConflictError) as exc_info:
        await service.create(AdminUserCreate(email="ops@example.com", password="password"))
    assert exc_info.value.code == "admin_email_exists"


async def test_set_status_revokes_sessions_when_disabled(
    service: AdminUserService, repository: AsyncMock, auth_repository: AsyncMock
) -> None:
    admin = _admin()
    repository.get_by_id.return_value = admin
    result = await service.set_status(admin.id, is_active=False)
    assert result.is_active is False
    auth_repository.revoke_sessions_for_admin.assert_awaited_once_with(admin.id)


async def test_cannot_disable_super_admin(service: AdminUserService, repository: AsyncMock) -> None:
    admin = _admin(role=AdminRole.SUPER_ADMIN)
    repository.get_by_id.return_value = admin
    with pytest.raises(ForbiddenError) as exc_info:
        await service.set_status(admin.id, is_active=False)
    assert exc_info.value.code == "super_admin_protected"


async def test_cannot_change_own_account(service: AdminUserService, repository: AsyncMock) -> None:
    admin = _admin(admin_id=_ACTOR_ID)
    repository.get_by_id.return_value = admin
    with pytest.raises(ForbiddenError) as exc_info:
        await service.set_status(admin.id, is_active=False)
    assert exc_info.value.code == "admin_self_mutation"


async def test_soft_delete_is_idempotent(
    service: AdminUserService, repository: AsyncMock, auth_repository: AsyncMock
) -> None:
    admin = _admin()
    admin.deleted_at = datetime.now(UTC)
    repository.get_by_id.return_value = admin
    result = await service.soft_delete(admin.id)
    assert result.deleted_at is not None
    auth_repository.revoke_sessions_for_admin.assert_not_awaited()


async def test_get_missing_admin_raises_not_found(
    service: AdminUserService, repository: AsyncMock
) -> None:
    repository.get_by_id.return_value = None
    with pytest.raises(NotFoundError) as exc_info:
        await service.get(uuid.uuid4())
    assert exc_info.value.code == "admin_user_not_found"
