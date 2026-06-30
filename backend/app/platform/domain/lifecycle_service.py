"""Reusable lifecycle operations for module services (composition, not inheritance)."""

from __future__ import annotations

import uuid
from typing import Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.platform.db.base import Base
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.persistence.filters import LifecycleListFilters
from app.platform.persistence.lifecycle import is_soft_deleted, mark_soft_deleted


class _LifecycleRepository[ModelT: Base](Protocol):
    async def get_by_id(
        self,
        entity_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> ModelT | None: ...

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        filters: LifecycleListFilters | None = None,
    ) -> list[ModelT]: ...

    async def count(self, *, filters: LifecycleListFilters | None = None) -> int: ...

    async def flush(self) -> None: ...


async def get_or_raise[ModelT: Base](
    repository: _LifecycleRepository[ModelT],
    entity_id: uuid.UUID,
    *,
    message: str,
    code: str,
    include_deleted: bool = False,
) -> ModelT:
    """Fetch by id or raise ``NotFoundError``.

    By default soft-deleted rows are treated as not found (public read path).
    """
    entity = await repository.get_by_id(entity_id, include_deleted=include_deleted)
    if entity is None:
        raise NotFoundError(message=message, code=code)
    return entity


async def list_paginated[ModelT: Base](
    repository: _LifecycleRepository[ModelT],
    params: ListParams,
) -> PaginatedResult[ModelT]:
    """List entities with lifecycle filters and pagination metadata."""
    filters = LifecycleListFilters(
        include_deleted=params.include_deleted,
        is_active=params.is_active,
    )
    items = await repository.list_page(
        limit=params.limit,
        offset=params.offset,
        filters=filters,
    )
    total = await repository.count(filters=filters)
    return PaginatedResult(
        items=items,
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


def require_not_deleted(
    entity: object,
    *,
    message: str,
    code: str,
) -> None:
    """Raise ``ConflictError`` when the entity is soft-deleted."""
    if is_soft_deleted(entity):
        raise ConflictError(message=message, code=code)


async def soft_delete[ModelT: Base](
    session: AsyncSession,
    repository: _LifecycleRepository[ModelT],
    entity_id: uuid.UUID,
    *,
    not_found_message: str,
    not_found_code: str,
    deleted_by: uuid.UUID | None = None,
) -> ModelT:
    """Soft-delete an entity (idempotent when already deleted)."""
    entity = await get_or_raise(
        repository,
        entity_id,
        message=not_found_message,
        code=not_found_code,
        include_deleted=True,
    )
    if is_soft_deleted(entity):
        return entity

    mark_soft_deleted(cast(Any, entity), deleted_by=deleted_by)
    return await flush_commit_refresh(session, repository, entity)


async def update_active_status[ModelT: Base](
    session: AsyncSession,
    repository: _LifecycleRepository[ModelT],
    entity_id: uuid.UUID,
    is_active: bool,
    *,
    not_found_message: str,
    not_found_code: str,
    deleted_message: str,
    deleted_code: str,
) -> ModelT:
    """Toggle ``is_active`` on a non-deleted entity (no-op when unchanged)."""
    entity = await get_or_raise(
        repository,
        entity_id,
        message=not_found_message,
        code=not_found_code,
        include_deleted=True,
    )
    require_not_deleted(entity, message=deleted_message, code=deleted_code)

    if entity.is_active == is_active:  # type: ignore[attr-defined]
        return entity

    entity.is_active = is_active  # type: ignore[attr-defined]
    return await flush_commit_refresh(session, repository, entity)
