"""Focused Super Admin authentication unit coverage without infrastructure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.config import AuthConfig
from app.core.exceptions import UnauthorizedError
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminRole, AdminUser
from app.modules.admin_auth.security import hash_password
from app.modules.admin_auth.service import AdminAuthService

pytestmark = pytest.mark.unit


class _Repository:
    def __init__(self, admin: AdminUser | None) -> None:
        self.admin = admin
        self.sessions: dict[UUID, AdminSession] = {}

    async def get_admin_by_email(self, email: str) -> AdminUser | None:
        return self.admin if self.admin and self.admin.email == email else None

    async def get_admin(self, admin_id: UUID) -> AdminUser | None:
        return self.admin if self.admin and self.admin.id == admin_id else None

    async def get_active_session(self, session_id: UUID) -> AdminSession | None:
        session = self.sessions.get(session_id)
        if session and session.revoked_at is None and session.expires_at > datetime.now(UTC):
            return session
        return None

    async def get_session_by_refresh_hash(self, token_hash: str) -> AdminSession | None:
        for session in self.sessions.values():
            if (
                session.refresh_token_hash == token_hash
                and session.revoked_at is None
                and session.expires_at > datetime.now(UTC)
            ):
                return session
        return None

    def add(self, entity: AdminUser | AdminSession) -> None:
        if isinstance(entity, AdminSession):
            self.sessions[entity.id] = entity

    async def commit(self) -> None:
        return None


def _admin(*, active: bool = True) -> AdminUser:
    return AdminUser(
        id=uuid4(),
        email="owner@example.com",
        password_hash=hash_password("correct horse battery staple"),
        role=AdminRole.SUPER_ADMIN.value,
        is_active=active,
    )


def _service(admin: AdminUser | None) -> tuple[AdminAuthService, _Repository]:
    repository = _Repository(admin)
    config = AuthConfig(
        enabled=True,
        key_pepper="machine-key-pepper-for-tests-at-least-32",
        admin_jwt_secret="admin-jwt-signing-secret-for-tests-32",
    )
    return AdminAuthService(repository, config), repository


async def test_login_then_current_admin_and_refresh_rotation() -> None:
    service, _ = _service(_admin())
    login = await service.login(email="owner@example.com", password="correct horse battery staple")
    first = await service.refresh(login.refresh_token)

    with pytest.raises(UnauthorizedError):
        await service.refresh(login.refresh_token)

    session = next(iter(service._repository.sessions.values()))  # type: ignore[attr-defined]
    current = await service.current_admin(admin_id=session.admin_user_id, session_id=session.id)
    assert current.email == "owner@example.com"
    assert first.refresh_token != login.refresh_token


@pytest.mark.parametrize(
    ("admin", "password"),
    [
        (None, "wrong"),
        (_admin(), "wrong"),
        (_admin(active=False), "correct horse battery staple"),
    ],
)
async def test_login_rejects_unknown_invalid_and_disabled_admin(
    admin: AdminUser | None, password: str
) -> None:
    service, _ = _service(admin)
    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        await service.login(email="owner@example.com", password=password)


async def test_logout_revokes_access_session() -> None:
    service, repository = _service(_admin())
    login = await service.login(email="owner@example.com", password="correct horse battery staple")
    session = next(iter(repository.sessions.values()))
    await service.logout(login.refresh_token)
    with pytest.raises(UnauthorizedError):
        await service.current_admin(admin_id=session.admin_user_id, session_id=session.id)


async def test_expired_refresh_token_is_rejected() -> None:
    service, repository = _service(_admin())
    login = await service.login(email="owner@example.com", password="correct horse battery staple")
    session = next(iter(repository.sessions.values()))
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(UnauthorizedError):
        await service.refresh(login.refresh_token)
