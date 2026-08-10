"""Cookie-backed Super Admin browser authentication endpoints."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response

from app.core.http.envelopes import ApiResponse
from app.dependencies.admin_auth import (
    ADMIN_ACCESS_COOKIE,
    ADMIN_CSRF_COOKIE,
    ADMIN_CSRF_HEADER,
    ADMIN_REFRESH_COOKIE,
    AdminAuthServiceDep,
    CurrentAdminDep,
)
from app.modules.admin_auth.schemas import AdminLoginRequest, CurrentAdminResponse
from app.modules.admin_auth.service import AdminTokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, tokens: AdminTokenPair, request: Request) -> None:
    config = request.app.state.settings.auth
    common = {
        "secure": config.admin_cookie_secure,
        "samesite": config.admin_cookie_samesite,
        "domain": config.admin_cookie_domain,
    }
    response.set_cookie(
        ADMIN_ACCESS_COOKIE,
        tokens.access_token,
        httponly=True,
        path="/api/v1",
        **common,
    )
    response.set_cookie(
        ADMIN_REFRESH_COOKIE,
        tokens.refresh_token,
        httponly=True,
        path="/api/v1/auth",
        **common,
    )
    response.set_cookie(ADMIN_CSRF_COOKIE, tokens.csrf_token, httponly=False, path="/", **common)


def _clear_auth_cookies(response: Response, request: Request) -> None:
    config = request.app.state.settings.auth
    common = {
        "secure": config.admin_cookie_secure,
        "samesite": config.admin_cookie_samesite,
        "domain": config.admin_cookie_domain,
    }
    response.delete_cookie(ADMIN_ACCESS_COOKIE, path="/api/v1", **common)
    response.delete_cookie(ADMIN_REFRESH_COOKIE, path="/api/v1/auth", **common)
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/", **common)


@router.post("/login", response_model=ApiResponse[CurrentAdminResponse])
async def login(
    body: AdminLoginRequest,
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
) -> ApiResponse[CurrentAdminResponse]:
    tokens = await service.login(email=body.email, password=body.password)
    _set_auth_cookies(response, tokens, request)
    admin = await service.current_admin_from_email(body.email)
    return ApiResponse.ok(CurrentAdminResponse.model_validate(admin))


@router.post("/refresh", response_model=ApiResponse[None])
async def refresh(
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
    refresh_token: Annotated[str | None, Cookie(alias=ADMIN_REFRESH_COOKIE)] = None,
) -> ApiResponse[None]:
    if not refresh_token:
        from app.core.exceptions import UnauthorizedError

        raise UnauthorizedError("Session is invalid or expired.")
    tokens = await service.refresh(refresh_token)
    _set_auth_cookies(response, tokens, request)
    return ApiResponse.ok(message="Session refreshed")


@router.post("/logout", response_model=ApiResponse[None])
async def logout(
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
    refresh_token: Annotated[str | None, Cookie(alias=ADMIN_REFRESH_COOKIE)] = None,
) -> ApiResponse[None]:
    csrf_cookie = request.cookies.get(ADMIN_CSRF_COOKIE)
    csrf_header = request.headers.get(ADMIN_CSRF_HEADER)
    if csrf_cookie and (not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header)):
        from app.core.exceptions import ForbiddenError

        raise ForbiddenError("CSRF validation failed.")
    await service.logout(refresh_token)
    _clear_auth_cookies(response, request)
    return ApiResponse.ok(message="Logged out")


@router.get("/me", response_model=ApiResponse[CurrentAdminResponse])
async def me(admin: CurrentAdminDep) -> ApiResponse[CurrentAdminResponse]:
    return ApiResponse.ok(CurrentAdminResponse.model_validate(admin))
