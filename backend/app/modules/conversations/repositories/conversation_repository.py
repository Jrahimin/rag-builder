"""Conversation persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, select

from app.models.conversation import Conversation
from app.platform.persistence.filters import (
    LifecycleListFilters,
    build_lifecycle_filters,
    not_deleted_filter,
)
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class ConversationRepository(ProjectScopedRepository[Conversation]):
    """Async CRUD for conversations within a single Project."""

    model = Conversation

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
    ) -> Conversation | None:
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
    ) -> list[Conversation]:
        list_filters = filters or LifecycleListFilters()
        stmt = self._scoped().where(*self._lifecycle_filters(list_filters))
        stmt = stmt.order_by(
            self.model.last_message_at.desc().nullslast(),
            self.model.created_at.desc(),
            self.model.id.desc(),
        )
        stmt = stmt.limit(limit).offset(offset)
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

    async def flush(self) -> None:
        await self._session.flush()
