"""Cookie-backed Super Admin browser authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response

from app.core.http.envelopes import ApiResponse
from app.dependencies.admin_auth import (
    AdminAuthServiceDep,
    AdminLoginRateLimiterDep,
    CurrentAdminDep,
    enforce_admin_login_rate_limit,
)
from app.modules.admin_auth.schemas import AdminLoginRequest, CurrentAdminResponse
from app.platform.http.admin_cookies import (
    ADMIN_REFRESH_COOKIE,
    clear_admin_auth_cookies,
    require_admin_csrf,
    set_admin_auth_cookies,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=ApiResponse[CurrentAdminResponse])
async def login(
    body: AdminLoginRequest,
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
    login_rate_limiter: AdminLoginRateLimiterDep,
) -> ApiResponse[CurrentAdminResponse]:
    await enforce_admin_login_rate_limit(
        request=request,
        email=body.email,
        limiter=login_rate_limiter,
        settings=request.app.state.settings,
    )
    tokens = await service.login(email=body.email, password=body.password)
    set_admin_auth_cookies(
        response,
        config=request.app.state.settings.auth,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        csrf_token=tokens.csrf_token,
    )
    admin = await service.current_admin_from_email(body.email)
    return ApiResponse.ok(CurrentAdminResponse.model_validate(admin))


@router.post("/refresh", response_model=ApiResponse[None])
async def refresh(
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
    refresh_token: Annotated[str | None, Cookie(alias=ADMIN_REFRESH_COOKIE)] = None,
) -> ApiResponse[None]:
    require_admin_csrf(request)
    if not refresh_token:
        from app.core.exceptions import UnauthorizedError

        raise UnauthorizedError("Session is invalid or expired.")
    tokens = await service.refresh(refresh_token)
    set_admin_auth_cookies(
        response,
        config=request.app.state.settings.auth,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        csrf_token=tokens.csrf_token,
    )
    return ApiResponse.ok(message="Session refreshed")


@router.post("/logout", response_model=ApiResponse[None])
async def logout(
    request: Request,
    response: Response,
    service: AdminAuthServiceDep,
    refresh_token: Annotated[str | None, Cookie(alias=ADMIN_REFRESH_COOKIE)] = None,
) -> ApiResponse[None]:
    require_admin_csrf(request)
    await service.logout(refresh_token)
    clear_admin_auth_cookies(response, config=request.app.state.settings.auth)
    return ApiResponse.ok(message="Logged out")


@router.get("/me", response_model=ApiResponse[CurrentAdminResponse])
async def me(admin: CurrentAdminDep) -> ApiResponse[CurrentAdminResponse]:
    return ApiResponse.ok(CurrentAdminResponse.model_validate(admin))
