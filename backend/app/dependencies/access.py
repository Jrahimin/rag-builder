"""Explicit policies for routes that may be used by an operator or an integration."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.dependencies.admin_auth import (
    ADMIN_ACCESS_COOKIE,
    AdminAuthServiceDep,
    current_admin,
)
from app.dependencies.auth import (
    get_rate_limiter,
    get_verified_key_cache,
    require_organization_api_key,
)
from app.dependencies.common import DbSessionDep, SettingsDep
from app.platform.auth.contracts import VerifiedKeyCache
from app.platform.domain.auth_context import AuthenticatedOrganization
from app.platform.http.admin_cookies import require_admin_csrf
from app.platform.http.auth_headers import extract_api_key
from app.platform.http.openapi_security import ORG_API_KEY_SCHEME, ORG_BEARER_SCHEME
from app.platform.rate_limit.contracts import RateLimiter


async def require_admin_or_organization(
    request: Request,
    settings: SettingsDep,
    session: DbSessionDep,
    admin_service: AdminAuthServiceDep,
    verified_key_cache: Annotated[VerifiedKeyCache, Depends(get_verified_key_cache)],
    rate_limiter: Annotated[RateLimiter | None, Depends(get_rate_limiter)],
    access_token: Annotated[str | None, Cookie(alias=ADMIN_ACCESS_COOKIE)] = None,
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(ORG_BEARER_SCHEME)] = None,
    _api_key_header: Annotated[str | None, Depends(ORG_API_KEY_SCHEME)] = None,
) -> AuthenticatedOrganization:
    """Allow a scoped Organization key or the Super Admin console, explicitly.

    An API key wins when both credentials are present so application traffic is
    never silently reclassified as human platform access.
    """
    if access_token and extract_api_key(request) is None:
        admin = await current_admin(settings, admin_service, access_token)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_admin_csrf(request)
        context = AuthenticatedOrganization(
            organization_id=None,
            api_key_id=None,
            organization_is_active=True,
            is_platform_admin=True,
        )
        request.state.authenticated_admin = admin
        request.state.authenticated_organization = context
        return context

    return await require_organization_api_key(
        request,
        settings,
        session,
        verified_key_cache,
        rate_limiter,
        _bearer,
        _api_key_header,
    )


AdminOrOrganizationDep = Annotated[
    AuthenticatedOrganization, Depends(require_admin_or_organization)
]
