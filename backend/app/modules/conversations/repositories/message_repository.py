"""Message persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_, update

from app.models.conversation_config_snapshot import ConversationConfigSnapshot
from app.models.message import Message, MessageRole
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
        before_created_at: datetime | None = None,
        before_id: uuid.UUID | None = None,
    ) -> list[Message]:
        if (before_created_at is None) != (before_id is None):
            raise ValueError("History boundary requires both created_at and id.")
        stmt = self._scoped().where(self.model.conversation_id == conversation_id)
        if before_created_at is not None and before_id is not None:
            stmt = stmt.where(
                tuple_(self.model.created_at, self.model.id) < (before_created_at, before_id)
            )
        stmt = stmt.order_by(self.model.created_at.desc(), self.model.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def flush(self) -> None:
        await self._session.flush()

    async def execution_samples(
        self,
        snapshot_id: uuid.UUID | None = None,
        *,
        configuration_hash: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read only completed timings for the exact immutable configuration."""
        if snapshot_id is None and configuration_hash is None:
            return []
        identity = (
            select(ConversationConfigSnapshot.configuration_hash)
            .where(
                ConversationConfigSnapshot.id == snapshot_id,
                ConversationConfigSnapshot.project_id == self._project_id,
            )
            .scalar_subquery()
            if snapshot_id is not None
            else configuration_hash
        )
        stmt = (
            select(self.model.id, self.model.message_metadata)
            .join(
                ConversationConfigSnapshot,
                self.model.config_snapshot_id == ConversationConfigSnapshot.id,
            )
            .where(
                self.model.project_id == self._project_id,
                ConversationConfigSnapshot.configuration_hash == identity,
                ConversationConfigSnapshot.project_id == self._project_id,
                self.model.role == MessageRole.ASSISTANT,
            )
            .order_by(self.model.created_at.desc(), self.model.id.desc())
            .limit(min(limit, 200))
        )
        result = await self._session.execute(stmt)
        return [{"message_id": str(row[0]), "metadata": row[1]} for row in result.all()]

    async def record_completed_persistence(
        self, message: Message, metadata: dict[str, Any]
    ) -> None:
        await self._session.execute(
            update(self.model)
            .where(self.model.project_id == self._project_id, self.model.id == message.id)
            .values(message_metadata=metadata)
            .execution_options(synchronize_session=False)
        )
