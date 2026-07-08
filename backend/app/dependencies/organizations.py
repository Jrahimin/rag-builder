"""FastAPI dependencies for the Organizations module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.config import get_settings
from app.dependencies.auth import get_verified_key_cache
from app.dependencies.common import DbSessionDep
from app.modules.organizations.repositories.api_key_repository import ApiKeyRepository
from app.modules.organizations.repositories.organization_repository import OrganizationRepository
from app.modules.organizations.services.api_key_service import ApiKeyService
from app.modules.organizations.services.organization_service import OrganizationService
from app.platform.auth.contracts import AuthEventPublisher, VerifiedKeyCache
from app.platform.infra.auth.verified_key_cache_event_handler import VerifiedKeyCacheEventHandler


def get_organization_repository(session: DbSessionDep) -> OrganizationRepository:
    return OrganizationRepository(session)


def get_api_key_repository(session: DbSessionDep) -> ApiKeyRepository:
    return ApiKeyRepository(session)


def get_auth_event_publisher(
    verified_key_cache: Annotated[VerifiedKeyCache, Depends(get_verified_key_cache)],
) -> AuthEventPublisher:
    return VerifiedKeyCacheEventHandler(verified_key_cache)


def get_organization_service(
    session: DbSessionDep,
    repository: Annotated[OrganizationRepository, Depends(get_organization_repository)],
    auth_events: Annotated[AuthEventPublisher, Depends(get_auth_event_publisher)],
) -> OrganizationService:
    return OrganizationService(
        session=session,
        repository=repository,
        auth_events=auth_events,
    )


def get_api_key_service(
    session: DbSessionDep,
    api_key_repository: Annotated[ApiKeyRepository, Depends(get_api_key_repository)],
    org_repository: Annotated[OrganizationRepository, Depends(get_organization_repository)],
    auth_events: Annotated[AuthEventPublisher, Depends(get_auth_event_publisher)],
) -> ApiKeyService:
    settings = get_settings()
    pepper = settings.auth.key_pepper
    if not pepper:
        msg = "APE_AUTH__KEY_PEPPER is required when authentication is enabled."
        raise RuntimeError(msg)
    return ApiKeyService(
        session=session,
        api_key_repository=api_key_repository,
        organization_repository=org_repository,
        key_pepper=pepper,
        auth_events=auth_events,
    )


OrganizationServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]
ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]
