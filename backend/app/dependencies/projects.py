"""FastAPI dependencies for the Projects module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.audit import DatabaseAuditRecorder
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.dependencies.access import AdminOrOrganizationDep
from app.dependencies.admin_auth import CurrentAdminDep
from app.dependencies.auth import get_verified_key_cache
from app.dependencies.common import DbSessionDep
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.services.project_ai_config_service import (
    ProjectAdministrationService,
)
from app.modules.projects.services.project_service import ProjectService
from app.platform.auth.contracts import VerifiedKeyCache
from app.platform.infra.auth.verified_key_cache_event_handler import VerifiedKeyCacheEventHandler

ProjectIdPath = Annotated[uuid.UUID, Path()]


def get_project_repository(session: DbSessionDep) -> ProjectRepository:
    return ProjectRepository(session)


async def ensure_project_accessible(
    project_id: ProjectIdPath,
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    auth_org: AdminOrOrganizationDep,
) -> None:
    """Raise when the project is not accessible to the authenticated organization."""
    await _ensure_project_for_organization(
        project_id,
        project_repository,
        auth_org,
        include_deleted=False,
    )


async def ensure_project_owned(
    project_id: ProjectIdPath,
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    auth_org: AdminOrOrganizationDep,
) -> None:
    """Authorize a Project mutation while leaving deleted-state semantics to its service."""
    await _ensure_project_for_organization(
        project_id,
        project_repository,
        auth_org,
        include_deleted=True,
    )


async def _ensure_project_for_organization(
    project_id: uuid.UUID,
    project_repository: ProjectRepository,
    auth_org: AdminOrOrganizationDep,
    *,
    include_deleted: bool,
) -> None:
    project = await project_repository.get_by_id_for_organization(
        project_id,
        auth_org.organization_id,
        include_deleted=include_deleted,
    )
    if project is None:
        raise NotFoundError(
            message="Project not found.",
            code="project_not_found",
        )


def get_project_service(
    session: DbSessionDep,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    auth_org: AdminOrOrganizationDep,
) -> ProjectService:
    return ProjectService(
        session=session,
        repository=repository,
        organization_id=auth_org.organization_id,
        is_platform_admin=auth_org.is_platform_admin,
        audit=DatabaseAuditRecorder(session),
        actor_id=(str(auth_org.api_key_id) if auth_org.api_key_id is not None else None),
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_operator_project_service(
    session: DbSessionDep,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    admin: CurrentAdminDep,
) -> ProjectService:
    return ProjectService(
        session=session,
        repository=repository,
        organization_id=None,
        is_platform_admin=True,
        audit=DatabaseAuditRecorder(session),
        actor_id=str(admin.id),
    )


OperatorProjectServiceDep = Annotated[ProjectService, Depends(get_operator_project_service)]


def get_project_administration_service(
    session: DbSessionDep,
    project_id: ProjectIdPath,
    admin: CurrentAdminDep,
    verified_key_cache: Annotated[VerifiedKeyCache, Depends(get_verified_key_cache)],
) -> ProjectAdministrationService:
    return ProjectAdministrationService(
        session=session,
        project_id=project_id,
        repository=ProjectAIConfigRepository(session, project_id),
        settings=get_settings(),
        audit=DatabaseAuditRecorder(session),
        actor_id=str(admin.id),
        auth_events=VerifiedKeyCacheEventHandler(verified_key_cache),
    )


ProjectAdministrationServiceDep = Annotated[
    ProjectAdministrationService,
    Depends(get_project_administration_service),
]
