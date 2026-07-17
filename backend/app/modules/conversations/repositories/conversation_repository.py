"""Conversation persistence."""

from __future__ import annotations

from app.models.conversation import Conversation
from app.platform.persistence.filters import LifecycleListFilters
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class ConversationRepository(ProjectScopedRepository[Conversation]):
    """Async CRUD for conversations within a single Project."""

    model = Conversation

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
