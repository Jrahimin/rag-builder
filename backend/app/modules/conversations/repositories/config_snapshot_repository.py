"""Project-scoped access to immutable conversation configuration snapshots."""

from __future__ import annotations

import inspect
import uuid
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_config_snapshot import ConversationConfigSnapshot


class ConversationConfigSnapshotRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    async def get(self, snapshot_id: uuid.UUID) -> ConversationConfigSnapshot | None:
        result = await self._session.execute(
            select(ConversationConfigSnapshot).where(
                ConversationConfigSnapshot.id == snapshot_id,
                ConversationConfigSnapshot.project_id == self._project_id,
            )
        )
        return result.scalar_one_or_none()

    async def next_sequence(self, conversation_id: uuid.UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(ConversationConfigSnapshot.sequence), 0)).where(
                ConversationConfigSnapshot.project_id == self._project_id,
                ConversationConfigSnapshot.conversation_id == conversation_id,
            )
        )
        return int(value or 0) + 1

    async def add(self, snapshot: ConversationConfigSnapshot) -> None:
        result = cast(Any, self._session).add(snapshot)
        if inspect.isawaitable(result):  # Test doubles may model the whole session as async.
            await result
