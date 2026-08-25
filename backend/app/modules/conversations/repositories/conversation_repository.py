"""Conversation persistence."""

from __future__ import annotations

import uuid

from app.models.conversation import Conversation
from app.platform.persistence.filters import LifecycleListFilters
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class ConversationRepository(ProjectScopedRepository[Conversation]):
    """Async CRUD for conversations within a single Project."""

    model = Conversation

    async def get_by_id(
        self,
        entity_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> Conversation | None:
        """Fetch a conversation with all scalar state explicitly loaded.

        Chat dependencies read the captured configuration fields before doing
        any further work.  Refreshing the identity-mapped row here prevents an
        expired attribute from triggering implicit async IO (and therefore a
        ``MissingGreenlet``) when a session has reused the instance.
        """
        conversation = await super().get_by_id(
            entity_id,
            include_deleted=include_deleted,
            for_update=for_update,
        )
        if conversation is not None:
            await self._session.refresh(conversation)
        return conversation

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
