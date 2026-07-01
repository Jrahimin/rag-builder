"""Project business orchestration and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError
from app.models.project import Project
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.schemas.project import ProjectCreate, ProjectUpdate
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
    toggle_active_status,
)
from app.platform.domain.lifecycle_service import (
    soft_delete as soft_delete_entity,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult

_NOT_FOUND = {"message": "Project not found.", "code": "project_not_found"}
_DELETED = {"message": "Cannot modify a deleted project.", "code": "project_deleted"}


def _name_conflict() -> ConflictError:
    return ConflictError(
        message="A project with this name already exists.",
        code="project_name_conflict",
    )


class ProjectService:
    """Orchestrates Project CRUD, status updates, and soft delete."""

    def __init__(self, session: AsyncSession, repository: ProjectRepository) -> None:
        self._session = session
        self._repository = repository

    async def create(self, data: ProjectCreate) -> Project:
        if await self._repository.exists_by_name(data.name):
            raise _name_conflict()

        project = Project(
            name=data.name,
            description=data.description,
            is_active=True,
        )
        self._repository.add(project)
        return await flush_commit_refresh(
            self._session,
            self._repository,
            project,
            on_integrity=_name_conflict,
        )

    async def get(self, project_id: uuid.UUID) -> Project:
        return await get_or_raise(
            self._repository,
            project_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
        )

    async def list(self, params: ListParams) -> PaginatedResult[Project]:
        return await list_paginated(self._repository, params)

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        if not data.model_fields_set:
            raise BadRequestError(
                message="At least one field must be provided.",
                code="empty_update",
            )

        project = await self._require_mutable(project_id)

        if data.name is not None and data.name != project.name:
            if await self._repository.exists_by_name(data.name, exclude_id=project.id):
                raise _name_conflict()
            project.name = data.name

        if "description" in data.model_fields_set:
            project.description = data.description

        return await flush_commit_refresh(
            self._session,
            self._repository,
            project,
            on_integrity=_name_conflict,
        )

    async def toggle_status(self, project_id: uuid.UUID) -> Project:
        return await toggle_active_status(
            self._session,
            self._repository,
            project_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
            deleted_message=_DELETED["message"],
            deleted_code=_DELETED["code"],
        )

    async def soft_delete(self, project_id: uuid.UUID) -> Project:
        return await soft_delete_entity(
            self._session,
            self._repository,
            project_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
        )

    async def _require_mutable(self, project_id: uuid.UUID) -> Project:
        project = await get_or_raise(
            self._repository,
            project_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(project, **_DELETED)
        return project
