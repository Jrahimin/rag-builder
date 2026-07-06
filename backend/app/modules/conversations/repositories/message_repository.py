"""Message persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.message import Message
from app.platform.persistence.filters import apply_deterministic_order
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class MessageRepository(ProjectScopedRepository[Message]):
    """Async CRUD for messages within a single Project."""

    model = Message

    async def list_by_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[Message]:
        stmt = (
            self._scoped()
            .where(self.model.conversation_id == conversation_id)
            .limit(limit)
            .offset(offset)
        )
        stmt = apply_deterministic_order(stmt, self.model)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_conversation(self, conversation_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.project_id == self._project_id)
            .where(self.model.conversation_id == conversation_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_recent_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int,
    ) -> list[Message]:
        stmt = (
            self._scoped()
            .where(self.model.conversation_id == conversation_id)
            .order_by(self.model.created_at.desc(), self.model.id.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def flush(self) -> None:
        await self._session.flush()
