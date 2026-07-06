"""FastAPI dependencies for the Projects module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends

from app.core.exceptions import NotFoundError
from app.dependencies.common import DbSessionDep
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.services.project_service import ProjectService


async def ensure_project_exists(
    project_repository: ProjectRepository,
    project_id: uuid.UUID,
) -> None:
    """Raise when the project id does not exist."""
    project = await project_repository.get_by_id(project_id)
    if project is None:
        raise NotFoundError(
            message="Project not found.",
            code="project_not_found",
        )


def get_project_repository(session: DbSessionDep) -> ProjectRepository:
    return ProjectRepository(session)


def get_project_service(
    session: DbSessionDep,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectService:
    return ProjectService(session=session, repository=repository)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
