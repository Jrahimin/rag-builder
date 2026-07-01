"""Document persistence — project-scoped with lifecycle list filters."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, select

from app.models.document import Document
from app.platform.persistence.filters import (
    LifecycleListFilters,
    apply_deterministic_order,
    build_lifecycle_filters,
    column_equals,
    not_deleted_filter,
)
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class DocumentRepository(ProjectScopedRepository[Document]):
    """Async CRUD for documents within a single Project."""

    model = Document

    def _lifecycle_filters(self, filters: LifecycleListFilters) -> list[ColumnElement[bool]]:
        return build_lifecycle_filters(
            self.model,
            include_deleted=filters.include_deleted,
            is_active=filters.is_active,
        )

    async def get_by_id(
        self,
        entity_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Document | None:
        id_col = self.model.id
        clauses: list[ColumnElement[bool]] = [id_col == entity_id]
        if not include_deleted and getattr(self.model, "deleted_at", None) is not None:
            clauses.append(not_deleted_filter(self.model))
        result = await self._session.execute(self._scoped().where(*clauses))
        return result.scalar_one_or_none()

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        filters: LifecycleListFilters | None = None,
    ) -> list[Document]:
        list_filters = filters or LifecycleListFilters()
        stmt = self._scoped().where(*self._lifecycle_filters(list_filters))
        stmt = apply_deterministic_order(stmt, self.model).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, filters: LifecycleListFilters | None = None) -> int:
        list_filters = filters or LifecycleListFilters()
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.project_id == self._project_id)
            .where(*self._lifecycle_filters(list_filters))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def exists_by_content_sha256(self, content_sha256: str) -> bool:
        clauses: list[ColumnElement[bool]] = [
            column_equals(self.model, "content_sha256", content_sha256),
            not_deleted_filter(self.model),
        ]
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.project_id == self._project_id)
            .where(*clauses)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0

    async def flush(self) -> None:
        await self._session.flush()
