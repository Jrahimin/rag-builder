"""Super Admin login, refresh rotation, and revocation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import AuthConfig
from app.core.exceptions import UnauthorizedError
from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser, is_operator_role
from app.modules.admin_auth.repository import AdminAuthRepository
from app.modules.admin_auth.schemas import AuthenticatedAdmin
from app.modules.admin_auth.security import (
    create_access_token,
    generate_refresh_token,
    hash_token,
    verify_password,
)

_INVALID_LOGIN = "Invalid email or password."
_INVALID_SESSION = "Session is invalid or expired."


@dataclass(frozen=True, slots=True)
class AdminTokenPair:
    access_token: str
    refresh_token: str
    csrf_token: str


class AdminAuthService:
    def __init__(self, repository: AdminAuthRepository, config: AuthConfig) -> None:
        self._repository = repository
        self._config = config

    async def login(self, *, email: str, password: str) -> AdminTokenPair:
        admin = await self._repository.get_admin_by_email(email)
        if (
            admin is None
            or not admin.is_active
            or admin.deleted_at is not None
            or not is_operator_role(admin.role)
            or not verify_password(password, admin.password_hash)
        ):
            raise UnauthorizedError(_INVALID_LOGIN)
        admin.last_login_at = datetime.now(UTC)
        tokens = self._new_tokens(admin)
        self._repository.add(
            AdminSession(
                id=tokens.session_id,
                admin_user_id=admin.id,
                refresh_token_hash=hash_token(tokens.refresh_token),
                expires_at=datetime.now(UTC)
                + timedelta(days=self._config.admin_refresh_token_expire_days),
            )
        )
        await self._repository.commit()
        return AdminTokenPair(tokens.access_token, tokens.refresh_token, tokens.csrf_token)

    async def refresh(self, refresh_token: str) -> AdminTokenPair:
        session = await self._repository.get_session_by_refresh_hash(hash_token(refresh_token))
        if session is None:
            raise UnauthorizedError(_INVALID_SESSION)
        admin = await self._repository.get_admin(session.admin_user_id)
        if (
            admin is None
            or not admin.is_active
            or admin.deleted_at is not None
            or not is_operator_role(admin.role)
        ):
            session.revoked_at = datetime.now(UTC)
            await self._repository.commit()
            raise UnauthorizedError(_INVALID_SESSION)
        tokens = self._new_tokens(admin, session.id)
        session.refresh_token_hash = hash_token(tokens.refresh_token)
        await self._repository.commit()
        return AdminTokenPair(tokens.access_token, tokens.refresh_token, tokens.csrf_token)

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        session = await self._repository.get_session_by_refresh_hash(hash_token(refresh_token))
        if session is not None:
            session.revoked_at = datetime.now(UTC)
            await self._repository.commit()

    async def current_admin(self, *, admin_id: UUID, session_id: UUID) -> AuthenticatedAdmin:
        session = await self._repository.get_active_session(session_id)
        admin = await self._repository.get_admin(admin_id)
        if (
            session is None
            or admin is None
            or session.admin_user_id != admin.id
            or not admin.is_active
            or admin.deleted_at is not None
            or not is_operator_role(admin.role)
        ):
            raise UnauthorizedError(_INVALID_SESSION)
        return AuthenticatedAdmin(
            id=admin.id,
            email=admin.email,
            role=admin.role,
            session_id=session.id,
            last_login_at=admin.last_login_at,
        )

    async def current_admin_from_email(self, email: str) -> AdminUser:
        admin = await self._repository.get_admin_by_email(email)
        if admin is None:
            raise UnauthorizedError(_INVALID_LOGIN)
        return admin

    def _new_tokens(self, admin: AdminUser, session_id: UUID | None = None) -> _NewTokens:
        from uuid import uuid4

        resolved_session_id = session_id or uuid4()
        return _NewTokens(
            session_id=resolved_session_id,
            access_token=create_access_token(
                admin_id=admin.id,
                email=admin.email,
                session_id=resolved_session_id,
                role=admin.role,
                config=self._config,
            ),
            refresh_token=generate_refresh_token(),
            csrf_token=generate_refresh_token(),
        )


@dataclass(frozen=True, slots=True)
class _NewTokens:
    session_id: UUID
    access_token: str
    refresh_token: str
    csrf_token: str
