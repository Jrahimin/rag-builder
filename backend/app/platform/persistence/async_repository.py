"""Generic async repository for unscoped entities (e.g. aggregate roots).

Project-owned entities must use :class:`ProjectScopedRepository` instead.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.platform.db.base import Base
from app.platform.persistence.filters import (
    LifecycleListFilters,
    apply_deterministic_order,
    build_lifecycle_filters,
    column_equals,
    not_deleted_filter,
)


class AsyncRepository[ModelT: Base]:
    """Reusable async CRUD for models with standard lifecycle columns."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_id(
        self,
        entity_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """Fetch by primary key.

        When ``include_deleted`` is false (default) and the model supports soft
        delete, rows with a non-null ``deleted_at`` are excluded.
        """
        id_col = getattr(self.model, "id", None)
        if id_col is None:
            msg = f"{self.model.__name__} must expose id (use UUIDPrimaryKeyMixin)"
            raise TypeError(msg)
        clauses: list[ColumnElement[bool]] = [id_col == entity_id]
        if not include_deleted and getattr(self.model, "deleted_at", None) is not None:
            clauses.append(not_deleted_filter(self.model))
        result = await self._session.execute(select(self.model).where(*clauses))
        return result.scalar_one_or_none()

    def _lifecycle_filters(self, filters: LifecycleListFilters) -> list[ColumnElement[bool]]:
        return build_lifecycle_filters(
            self.model,
            include_deleted=filters.include_deleted,
            is_active=filters.is_active,
        )

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        filters: LifecycleListFilters | None = None,
    ) -> list[ModelT]:
        list_filters = filters or LifecycleListFilters()
        stmt = select(self.model).where(*self._lifecycle_filters(list_filters))
        stmt = apply_deterministic_order(stmt, self.model).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, filters: LifecycleListFilters | None = None) -> int:
        list_filters = filters or LifecycleListFilters()
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(*self._lifecycle_filters(list_filters))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def exists_by_field(
        self,
        field_name: str,
        value: object,
        *,
        exclude_id: uuid.UUID | None = None,
        not_deleted: bool = True,
    ) -> bool:
        """Return whether a row matches ``field == value``, optionally among non-deleted rows."""
        clauses: list[ColumnElement[Any]] = [column_equals(self.model, field_name, value)]
        if not_deleted and getattr(self.model, "deleted_at", None) is not None:
            clauses.append(not_deleted_filter(self.model))
        id_col: InstrumentedAttribute[Any] | None = getattr(self.model, "id", None)
        if exclude_id is not None:
            if id_col is None:
                msg = f"{self.model.__name__} must expose id for exclude_id"
                raise TypeError(msg)
            clauses.append(id_col != exclude_id)
        stmt = select(func.count()).select_from(self.model).where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0

    def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        return entity

    async def flush(self) -> None:
        await self._session.flush()
