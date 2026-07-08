"""FastAPI dependencies for the Projects module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.exceptions import NotFoundError
from app.dependencies.auth import AuthenticatedOrganizationDep
from app.dependencies.common import DbSessionDep
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.services.project_service import ProjectService

ProjectIdPath = Annotated[uuid.UUID, Path()]


def get_project_repository(session: DbSessionDep) -> ProjectRepository:
    return ProjectRepository(session)


async def ensure_project_accessible(
    project_id: ProjectIdPath,
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    auth_org: AuthenticatedOrganizationDep,
) -> None:
    """Raise when the project is not accessible to the authenticated organization."""
    project = await project_repository.get_by_id_for_organization(
        project_id,
        auth_org.organization_id,
    )
    if project is None:
        raise NotFoundError(
            message="Project not found.",
            code="project_not_found",
        )


def get_project_service(
    session: DbSessionDep,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    auth_org: AuthenticatedOrganizationDep,
) -> ProjectService:
    return ProjectService(
        session=session,
        repository=repository,
        organization_id=auth_org.organization_id,
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


async def noop_ensure_project() -> None:
    """No-op guard for HTTP routes already protected by router-level dependencies."""
