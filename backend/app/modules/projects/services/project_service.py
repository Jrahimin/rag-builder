"""Project business orchestration and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.models.organization import Organization
from app.models.project import Project
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectOwnershipMigrationStatus,
    ProjectResponse,
    ProjectUpdate,
)
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
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
        is_platform_admin: bool = False,
        audit: AuditRecorder | None = None,
        actor_id: str | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._organization_id = organization_id
        self._is_platform_admin = is_platform_admin
        self._audit = audit
        self._actor_id = actor_id

    def _create_organization_id(self, requested: uuid.UUID | None) -> uuid.UUID:
        if self._is_platform_admin:
            if requested is None:
                raise BadRequestError(
                    message="Super Admin Project creation requires organization_id.",
                    code="project_organization_required",
                )
            return requested
        if self._organization_id is not None:
            if requested is not None and requested != self._organization_id:
                raise ForbiddenError(
                    message="A Project can only be created for the authenticated Organization.",
                    code="project_organization_forbidden",
                )
            return self._organization_id
        return requested or DEFAULT_ORGANIZATION_ID

    async def create(self, data: ProjectCreate) -> Project:
        org_id = self._create_organization_id(data.organization_id)
        organization_exists = await self._session.scalar(
            select(Organization.id).where(
                Organization.id == org_id,
                Organization.deleted_at.is_(None),
                Organization.is_active.is_(True),
            )
        )
        if organization_exists is None:
            raise NotFoundError(
                message="Organization not found.",
                code="organization_not_found",
            )
        if await self._repository.exists_by_name(data.name, organization_id=org_id):
            raise _name_conflict()

        project = Project(
            id=uuid.uuid4(),
            name=data.name,
            description=data.description,
            is_active=True,
            organization_id=org_id,
            ownership_locked=True,
        )
        self._repository.add(project)
        if self._audit is not None:
            try:
                await self._repository.flush()
            except IntegrityError:
                await self._session.rollback()
                raise _name_conflict() from None
            self._audit.record(
                event_type=AuditEventType.PROJECT_CREATED,
                actor_type=AuditActorType.OPERATOR,
                actor_id=self._actor_id,
                organization_id=org_id,
                project_id=project.id,
                resource_type="project",
                resource_id=project.id,
                outcome=AuditOutcome.SUCCESS,
                detail={"ownership_locked": True},
            )
        return await flush_commit_refresh(
            self._session,
            self._repository,
            project,
            on_integrity=_name_conflict,
        )

    async def get(self, project_id: uuid.UUID, *, include_deleted: bool = False) -> Project:
        project = await self._repository.get_by_id_for_organization(
            project_id,
            self._organization_id,
            include_deleted=include_deleted,
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

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        params: ListParams,
    ) -> PaginatedResult[Project]:
        if not self._is_platform_admin:
            raise ForbiddenError(
                message="Only Super Admins can inspect another Organization's Projects.",
                code="project_organization_forbidden",
            )
        filters = LifecycleListFilters(
            include_deleted=params.include_deleted,
            is_active=params.is_active,
        )
        items = await self._repository.list_page(
            limit=params.limit,
            offset=params.offset,
            filters=filters,
            organization_id=organization_id,
        )
        total = await self._repository.count(
            filters=filters,
            organization_id=organization_id,
        )
        return PaginatedResult(items=items, total=total, limit=params.limit, offset=params.offset)

    async def ownership_migration_status(self) -> ProjectOwnershipMigrationStatus:
        if not self._is_platform_admin:
            raise ForbiddenError(
                message="Only Super Admins can inspect ownership migration status.",
                code="project_ownership_forbidden",
            )
        projects = await self._repository.list_unlocked()
        locked = await self._repository.count_locked(locked=True)
        unlocked = await self._repository.count_locked(locked=False)
        return ProjectOwnershipMigrationStatus(
            total_projects=locked + unlocked,
            locked_projects=locked,
            legacy_unlocked_projects=unlocked,
            default_organization_unlocked_projects=(
                await self._repository.count_default_organization_unlocked(DEFAULT_ORGANIZATION_ID)
            ),
            projects=[ProjectResponse.model_validate(project) for project in projects],
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

        self._record(
            project,
            AuditEventType.PROJECT_UPDATED,
            detail={"fields": sorted(data.model_fields_set)},
        )

        return await flush_commit_refresh(
            self._session,
            self._repository,
            project,
            on_integrity=_name_conflict,
        )

    async def toggle_status(self, project_id: uuid.UUID) -> Project:
        project = await self._require_mutable(project_id)
        return await self.set_status(project_id, is_active=not project.is_active)

    async def set_status(self, project_id: uuid.UUID, *, is_active: bool) -> Project:
        project = await self._require_mutable(project_id)
        if project.is_active == is_active:
            return project
        project.is_active = is_active
        self._record(
            project,
            AuditEventType.PROJECT_STATUS_CHANGED,
            detail={"is_active": is_active},
        )
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
        self._record(
            project,
            AuditEventType.PROJECT_ARCHIVED,
            detail={"archived": True},
        )
        return await flush_commit_refresh(self._session, self._repository, project)

    async def restore(self, project_id: uuid.UUID) -> Project:
        project = await self._repository.get_by_id_for_organization(
            project_id,
            self._organization_id,
            include_deleted=True,
        )
        if project is None:
            raise NotFoundError(message=_NOT_FOUND["message"], code=_NOT_FOUND["code"])
        if project.deleted_at is None:
            return project
        project.deleted_at = None
        project.deleted_by = None
        project.is_active = False
        self._record(
            project,
            AuditEventType.PROJECT_RESTORED,
            detail={"is_active": False},
        )
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

    def _record(
        self,
        project: Project,
        event_type: AuditEventType,
        *,
        detail: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            actor_id=self._actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            outcome=AuditOutcome.SUCCESS,
            detail=detail,
        )
