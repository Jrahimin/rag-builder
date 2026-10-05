"""Project-scoped diagnostic persistence with strict read/write expiry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_diagnostic import MessageDiagnostic


class MessageDiagnosticRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID):
        self.session = session
        self.project_id = project_id

    async def expire_payloads(self, now: datetime | None = None) -> None:
        await self.session.execute(
            update(MessageDiagnostic)
            .where(
                MessageDiagnostic.project_id == self.project_id,
                MessageDiagnostic.expires_at <= (now or datetime.now(UTC)),
                MessageDiagnostic.payload.is_not(None),
            )
            .values(payload=None)
        )

    async def get(self, message_id: uuid.UUID) -> MessageDiagnostic | None:
        await self.expire_payloads()
        return await self.session.scalar(
            select(MessageDiagnostic).where(
                MessageDiagnostic.project_id == self.project_id,
                MessageDiagnostic.message_id == message_id,
            )
        )

    def add(self, record: MessageDiagnostic) -> None:
        self.session.add(record)
