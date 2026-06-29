"""FastAPI dependencies for the Projects module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.dependencies.common import DbSessionDep
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.services.project_service import ProjectService


def get_project_repository(session: DbSessionDep) -> ProjectRepository:
    return ProjectRepository(session)


def get_project_service(
    session: DbSessionDep,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectService:
    return ProjectService(session=session, repository=repository)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
