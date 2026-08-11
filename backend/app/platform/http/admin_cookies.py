"""Small browser-session helpers for Super Admin HTTP endpoints and policies."""

from __future__ import annotations

import hmac

from fastapi import Request, Response

from app.core.config import AuthConfig
from app.core.exceptions import ForbiddenError

ADMIN_ACCESS_COOKIE = "ape_admin_access"
ADMIN_REFRESH_COOKIE = "ape_admin_refresh"
ADMIN_CSRF_COOKIE = "ape_admin_csrf"
ADMIN_CSRF_HEADER = "X-CSRF-Token"


def set_admin_auth_cookies(
    response: Response,
    *,
    config: AuthConfig,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    """Set the three cookie values that make up an admin browser session."""
    response.set_cookie(
        ADMIN_ACCESS_COOKIE,
        access_token,
        httponly=True,
        path="/api/v1",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )
    response.set_cookie(
        ADMIN_REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        path="/api/v1/auth",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        csrf_token,
        httponly=False,
        path="/",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )


def clear_admin_auth_cookies(response: Response, *, config: AuthConfig) -> None:
    """Expire all browser-session cookies with the exact paths used when setting them."""
    response.delete_cookie(
        ADMIN_ACCESS_COOKIE,
        path="/api/v1",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )
    response.delete_cookie(
        ADMIN_REFRESH_COOKIE,
        path="/api/v1/auth",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )
    response.delete_cookie(
        ADMIN_CSRF_COOKIE,
        path="/",
        secure=config.admin_cookie_secure,
        samesite=config.admin_cookie_samesite,
        domain=config.admin_cookie_domain,
    )


def require_admin_csrf(request: Request) -> None:
    """Enforce the double-submit CSRF check for unsafe cookie-authenticated requests."""
    csrf_cookie = request.cookies.get(ADMIN_CSRF_COOKIE)
    csrf_header = request.headers.get(ADMIN_CSRF_HEADER)
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise ForbiddenError("CSRF validation failed.")
