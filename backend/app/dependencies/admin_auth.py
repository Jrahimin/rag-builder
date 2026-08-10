"""Explicit FastAPI dependencies for browser-based Super Admin access."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Request

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.dependencies.common import DbSessionDep, SettingsDep
from app.models.admin_user import AdminRole
from app.modules.admin_auth.repository import AdminAuthRepository
from app.modules.admin_auth.schemas import AuthenticatedAdmin
from app.modules.admin_auth.security import decode_access_token
from app.modules.admin_auth.service import AdminAuthService

ADMIN_ACCESS_COOKIE = "ape_admin_access"
ADMIN_REFRESH_COOKIE = "ape_admin_refresh"
ADMIN_CSRF_COOKIE = "ape_admin_csrf"
ADMIN_CSRF_HEADER = "X-CSRF-Token"


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


async def require_super_admin(request: Request, admin: CurrentAdminDep) -> AuthenticatedAdmin:
    """Require the single Phase-1 human role and verify unsafe cookie requests' CSRF token."""
    if admin.role != AdminRole.SUPER_ADMIN.value:
        raise ForbiddenError("Super Admin access is required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf_cookie = request.cookies.get(ADMIN_CSRF_COOKIE)
        csrf_header = request.headers.get(ADMIN_CSRF_HEADER)
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            raise ForbiddenError("CSRF validation failed.")
    return admin
