"""Project persistence — extends the shared async repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.project import Project
from app.platform.persistence.async_repository import AsyncRepository
from app.platform.persistence.filters import LifecycleListFilters, build_lifecycle_filters


class ProjectRepository(AsyncRepository[Project]):
    """Async CRUD for the Project aggregate root."""

    model = Project

    async def get_by_id_for_organization(
        self,
        project_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        *,
        include_deleted: bool = False,
    ) -> Project | None:
        """Single query: id + organization_id + not deleted."""
        clauses = [Project.id == project_id]
        if organization_id is not None:
            clauses.append(Project.organization_id == organization_id)
        if not include_deleted:
            from app.platform.persistence.filters import not_deleted_filter

            clauses.append(not_deleted_filter(Project))
        result = await self._session.execute(select(Project).where(*clauses))
        return result.scalar_one_or_none()

    async def exists_by_name(
        self,
        name: str,
        *,
        organization_id: uuid.UUID | None = None,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        from sqlalchemy import func

        from app.platform.persistence.filters import column_equals, not_deleted_filter

        clauses = [column_equals(self.model, "name", name)]
        if getattr(self.model, "deleted_at", None) is not None:
            clauses.append(not_deleted_filter(self.model))
        if organization_id is not None:
            clauses.append(Project.organization_id == organization_id)
        if exclude_id is not None:
            clauses.append(Project.id != exclude_id)
        stmt = select(func.count()).select_from(self.model).where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        filters: LifecycleListFilters | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> list[Project]:
        list_filters = filters or LifecycleListFilters()
        clauses = self._lifecycle_filters(list_filters)
        if organization_id is not None:
            clauses.append(Project.organization_id == organization_id)
        from app.platform.persistence.filters import apply_deterministic_order

        stmt = select(Project).where(*clauses)
        stmt = apply_deterministic_order(stmt, Project).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        filters: LifecycleListFilters | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> int:
        from sqlalchemy import func

        list_filters = filters or LifecycleListFilters()
        clauses = self._lifecycle_filters(list_filters)
        if organization_id is not None:
            clauses.append(Project.organization_id == organization_id)
        stmt = select(func.count()).select_from(Project).where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())
