"""Authentication dependencies — API key verification and rate limiting."""

from __future__ import annotations

import hmac
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, VerifyCacheBackend
from app.core.exceptions import RateLimitError, UnauthorizedError
from app.core.logging import get_logger
from app.dependencies.common import DbSessionDep, SettingsDep, get_redis_connectivity
from app.modules.organizations.repositories.api_key_repository import ApiKeyRepository
from app.platform.auth.contracts import CachedVerifiedKey, VerifiedKeyCache
from app.platform.domain.api_key_crypto import hash_key, verify_key
from app.platform.domain.auth_context import AuthenticatedOrganization
from app.platform.http.auth_headers import extract_api_key, is_api_key_format
from app.platform.http.openapi_security import (
    ADMIN_BEARER_SCHEME,
    ORG_API_KEY_SCHEME,
    ORG_BEARER_SCHEME,
)
from app.platform.infra.auth.memory_verified_key_cache import get_memory_verified_key_cache
from app.platform.infra.auth.redis_verified_key_cache import RedisVerifiedKeyCache
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.infra.rate_limit.redis_rate_limiter import RedisRateLimiter
from app.platform.rate_limit.contracts import RateLimiter

log = get_logger(__name__)


def _unauthorized() -> UnauthorizedError:
    return UnauthorizedError(message="Authentication is required.", code="unauthorized")


@lru_cache
def _get_cached_memory_verified_key_cache() -> VerifiedKeyCache:
    return get_memory_verified_key_cache()


def get_verified_key_cache(
    settings: SettingsDep,
    redis: Annotated[RedisConnectivity, Depends(get_redis_connectivity)],
) -> VerifiedKeyCache:
    if not settings.auth.verify_cache_enabled:
        return get_memory_verified_key_cache()
    if settings.auth.verify_cache_backend is VerifyCacheBackend.MEMORY:
        return _get_cached_memory_verified_key_cache()
    return RedisVerifiedKeyCache(redis.client)


def get_rate_limiter(
    settings: SettingsDep,
    redis: Annotated[RedisConnectivity, Depends(get_redis_connectivity)],
) -> RateLimiter | None:
    if not settings.auth.rate_limit_enabled:
        return None
    return RedisRateLimiter(
        redis.client,
        max_requests=settings.auth.rate_limit_requests,
        window_seconds=settings.auth.rate_limit_window_seconds,
    )


async def _apply_rate_limit(
    settings: Settings,
    rate_limiter: RateLimiter | None,
    organization_id: uuid.UUID,
) -> None:
    if not settings.auth.rate_limit_enabled or rate_limiter is None:
        return
    try:
        result = await rate_limiter.check(organization_id)
    except Exception as exc:
        if settings.auth.rate_limit_fail_open:
            log.warning("rate_limit_check_failed", error=str(exc))
            return
        raise
    if not result.allowed:
        raise RateLimitError(
            message="Rate limit exceeded.",
            retry_after_seconds=result.retry_after_seconds,
        )


async def _resolve_organization_from_key(
    *,
    session: AsyncSession,
    settings: Settings,
    raw_key: str,
    verified_key_cache: VerifiedKeyCache,
) -> AuthenticatedOrganization:
    pepper = settings.auth.key_pepper
    if not pepper:
        raise _unauthorized()

    key_hash = hash_key(raw_key, pepper)

    if settings.auth.verify_cache_enabled:
        cached = await verified_key_cache.get(key_hash)
        if cached is not None:
            if not cached.organization_is_active:
                log.warning("organization_inactive", organization_id=str(cached.organization_id))
                raise _unauthorized()
            return AuthenticatedOrganization(
                organization_id=cached.organization_id,
                api_key_id=cached.api_key_id,
                organization_is_active=cached.organization_is_active,
            )

    api_key_repository = ApiKeyRepository(session)
    api_key = await api_key_repository.get_by_key_hash(key_hash)
    if api_key is None or not verify_key(raw_key, pepper, api_key.key_hash):
        raise _unauthorized()

    organization = await api_key_repository.get_organization_for_key(api_key)
    if organization is None or not organization.is_active or organization.deleted_at is not None:
        log.warning("organization_inactive", organization_id=str(api_key.organization_id))
        raise _unauthorized()

    if settings.auth.verify_cache_enabled:
        await verified_key_cache.set(
            key_hash,
            CachedVerifiedKey(
                organization_id=organization.id,
                organization_is_active=organization.is_active,
                api_key_id=api_key.id,
            ),
            ttl_seconds=settings.auth.verify_cache_ttl_seconds,
        )

    await api_key_repository.touch_last_used(api_key)
    await session.commit()

    return AuthenticatedOrganization(
        organization_id=organization.id,
        api_key_id=api_key.id,
        organization_is_active=organization.is_active,
    )


async def require_admin_api_key(
    request: Request,
    settings: SettingsDep,
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(ADMIN_BEARER_SCHEME)] = None,
) -> None:
    """Validate the deployment admin bootstrap API key."""
    if not settings.auth.enabled:
        return

    raw_key = extract_api_key(request)
    if raw_key is None:
        raise _unauthorized()

    admin_key = settings.auth.admin_api_key
    if not admin_key:
        raise _unauthorized()

    if not hmac.compare_digest(raw_key, admin_key):
        raise _unauthorized()


async def require_organization_api_key(
    request: Request,
    settings: SettingsDep,
    session: DbSessionDep,
    verified_key_cache: Annotated[VerifiedKeyCache, Depends(get_verified_key_cache)],
    rate_limiter: Annotated[RateLimiter | None, Depends(get_rate_limiter)],
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(ORG_BEARER_SCHEME)] = None,
    _api_key_header: Annotated[str | None, Depends(ORG_API_KEY_SCHEME)] = None,
) -> AuthenticatedOrganization:
    """Validate an Organization API key and apply rate limiting."""
    if not settings.auth.enabled:
        return AuthenticatedOrganization(
            organization_id=None,
            api_key_id=None,
            organization_is_active=True,
        )

    raw_key = extract_api_key(request)
    if raw_key is None or not is_api_key_format(raw_key):
        raise _unauthorized()

    auth_org = await _resolve_organization_from_key(
        session=session,
        settings=settings,
        raw_key=raw_key,
        verified_key_cache=verified_key_cache,
    )
    request.state.authenticated_organization = auth_org

    if auth_org.organization_id is not None:
        await _apply_rate_limit(settings, rate_limiter, auth_org.organization_id)
    return auth_org


AuthenticatedOrganizationDep = Annotated[
    AuthenticatedOrganization, Depends(require_organization_api_key)
]
