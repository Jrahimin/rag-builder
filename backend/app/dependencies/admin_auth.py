"""Explicit FastAPI dependencies for browser-based Super Admin access."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Request

from app.composition.audit import DatabaseAuditRecorder
from app.core.exceptions import ForbiddenError, RateLimitError, UnauthorizedError
from app.dependencies.common import DbSessionDep, SettingsDep, get_redis_connectivity
from app.models.admin_user import AdminRole, is_operator_role
from app.modules.admin_auth.repositories.admin_user_repository import AdminUserRepository
from app.modules.admin_auth.repository import AdminAuthRepository
from app.modules.admin_auth.schemas import AuthenticatedAdmin
from app.modules.admin_auth.security import decode_access_token, hash_token
from app.modules.admin_auth.service import AdminAuthService
from app.modules.admin_auth.services.admin_user_service import AdminUserService
from app.platform.http.admin_cookies import ADMIN_ACCESS_COOKIE, require_admin_csrf
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.infra.rate_limit.redis_rate_limiter import RedisRateLimiter


def get_admin_auth_service(session: DbSessionDep, settings: SettingsDep) -> AdminAuthService:
    return AdminAuthService(AdminAuthRepository(session), settings.auth)


AdminAuthServiceDep = Annotated[AdminAuthService, Depends(get_admin_auth_service)]


async def current_admin(
    settings: SettingsDep,
    service: AdminAuthServiceDep,
    access_token: Annotated[str | None, Cookie(alias=ADMIN_ACCESS_COOKIE)] = None,
) -> AuthenticatedAdmin:
    if not settings.auth.enabled:
        # Development/test mode retains the established unauthenticated local
        # console workflow. Production startup rejects disabled auth.
        return AuthenticatedAdmin(
            id=UUID(int=0),
            email="local-development@ape.invalid",
            role=AdminRole.SUPER_ADMIN.value,
            session_id=UUID(int=0),
            last_login_at=None,
        )
    if not access_token:
        raise UnauthorizedError("Authentication is required.")
    try:
        payload = decode_access_token(access_token, config=settings.auth)
        return await service.current_admin(
            admin_id=UUID(str(payload["sub"])), session_id=UUID(str(payload["sid"]))
        )
    except (KeyError, ValueError):
        raise UnauthorizedError("Authentication is required.") from None


CurrentAdminDep = Annotated[AuthenticatedAdmin, Depends(current_admin)]


def get_admin_user_repository(session: DbSessionDep) -> AdminUserRepository:
    return AdminUserRepository(session)


def get_admin_user_service(
    session: DbSessionDep,
    repository: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
    admin: CurrentAdminDep,
) -> AdminUserService:
    return AdminUserService(
        session,
        repository,
        AdminAuthRepository(session),
        actor_id=admin.id,
        audit=DatabaseAuditRecorder(session),
    )


AdminUserServiceDep = Annotated[AdminUserService, Depends(get_admin_user_service)]


async def require_super_admin(request: Request, admin: CurrentAdminDep) -> AuthenticatedAdmin:
    """Require a platform operator session and verify unsafe cookie requests' CSRF token.

    SUPER_ADMIN and ADMIN currently have the same access. Module permissions come later.
    """
    if not is_operator_role(admin.role):
        raise ForbiddenError("Admin access is required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_admin_csrf(request)
    return admin


def get_admin_login_rate_limiter(
    settings: SettingsDep,
    redis: Annotated[RedisConnectivity, Depends(get_redis_connectivity)],
) -> RedisRateLimiter | None:
    if not settings.auth.rate_limit_enabled:
        return None
    return RedisRateLimiter(
        redis.client,
        max_requests=settings.auth.admin_login_rate_limit_requests,
        window_seconds=settings.auth.admin_login_rate_limit_window_seconds,
    )


AdminLoginRateLimiterDep = Annotated[RedisRateLimiter | None, Depends(get_admin_login_rate_limiter)]


async def enforce_admin_login_rate_limit(
    *, request: Request, email: str, limiter: RedisRateLimiter | None, settings: SettingsDep
) -> None:
    """Rate-limit login attempts by a non-reversible email/IP fingerprint."""
    if limiter is None:
        return
    client_ip = request.client.host if request.client else "unknown"
    fingerprint = hash_token(f"{email}:{client_ip}")
    try:
        result = await limiter.check_key(fingerprint, prefix="ape:ratelimit:admin-login:")
    except Exception:
        if settings.auth.rate_limit_fail_open:
            return
        raise
    if not result.allowed:
        raise RateLimitError(
            message="Too many login attempts. Try again later.",
            retry_after_seconds=result.retry_after_seconds,
        )
