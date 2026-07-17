"""Project business orchestration and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.project import Project
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.schemas.project import ProjectCreate, ProjectUpdate
from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID
from app.platform.domain.lifecycle_service import require_not_deleted
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.persistence.filters import LifecycleListFilters
from app.platform.persistence.lifecycle import is_soft_deleted, mark_soft_deleted

_NOT_FOUND = {"message": "Project not found.", "code": "project_not_found"}
_DELETED = {"message": "Cannot modify a deleted project.", "code": "project_deleted"}


def _name_conflict() -> ConflictError:
    return ConflictError(
        message="A project with this name already exists.",
        code="project_name_conflict",
    )


class ProjectService:
    """Orchestrates Project CRUD, status updates, and soft delete."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ProjectRepository,
        *,
        organization_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._organization_id = organization_id

    def _create_organization_id(self) -> uuid.UUID:
        return self._organization_id or DEFAULT_ORGANIZATION_ID

    async def create(self, data: ProjectCreate) -> Project:
        org_id = self._create_organization_id()
        if await self._repository.exists_by_name(data.name, organization_id=org_id):
            raise _name_conflict()

        project = Project(
            name=data.name,
            description=data.description,
            is_active=True,
            organization_id=org_id,
        )
        self._repository.add(project)
        return await flush_commit_refresh(
            self._session,
            self._repository,
            project,
            on_integrity=_name_conflict,
        )

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self._repository.get_by_id_for_organization(
            project_id,
            self._organization_id,
        )
        if project is None:
            raise NotFoundError(message=_NOT_FOUND["message"], code=_NOT_FOUND["code"])
        return project

    async def list(self, params: ListParams) -> PaginatedResult[Project]:
        filters = LifecycleListFilters(
            include_deleted=params.include_deleted,
            is_active=params.is_active,
        )
        items = await self._repository.list_page(
            limit=params.limit,
            offset=params.offset,
            filters=filters,
            organization_id=self._organization_id,
        )
        total = await self._repository.count(
            filters=filters,
            organization_id=self._organization_id,
        )
        return PaginatedResult(
            items=items,
            total=total,
            limit=params.limit,
            offset=params.offset,
        )

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        if not data.model_fields_set:
            raise BadRequestError(
                message="At least one field must be provided.",
                code="empty_update",
            )

        project = await self._require_mutable(project_id)

        if data.name is not None and data.name != project.name:
            if await self._repository.exists_by_name(
                data.name,
                organization_id=self._organization_id or project.organization_id,
                exclude_id=project.id,
            ):
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
        project = await self._require_mutable(project_id)
        project.is_active = not project.is_active
        return await flush_commit_refresh(self._session, self._repository, project)

    async def soft_delete(self, project_id: uuid.UUID) -> Project:
        project = await self._repository.get_by_id_for_organization(
            project_id,
            self._organization_id,
            include_deleted=True,
        )
        if project is None:
            raise NotFoundError(message=_NOT_FOUND["message"], code=_NOT_FOUND["code"])
        if is_soft_deleted(project):
            return project
        mark_soft_deleted(project)
        project.is_active = False
        return await flush_commit_refresh(self._session, self._repository, project)

    async def _require_mutable(self, project_id: uuid.UUID) -> Project:
        project = await self._repository.get_by_id_for_organization(
            project_id,
            self._organization_id,
            include_deleted=True,
        )
        if project is None:
            raise NotFoundError(message=_NOT_FOUND["message"], code=_NOT_FOUND["code"])
        require_not_deleted(project, **_DELETED)
        return project
