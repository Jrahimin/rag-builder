"""Application-owned expiry sweep; relational mutations remain project-scoped."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.message_diagnostic import MessageDiagnostic
from app.modules.conversations.repositories.message_diagnostic_repository import (
    MessageDiagnosticRepository,
)

logger = structlog.get_logger(__name__)


async def expire_message_diagnostics(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        projects = list(
            await session.scalars(
                select(MessageDiagnostic.project_id)
                .where(
                    MessageDiagnostic.payload.is_not(None),
                    MessageDiagnostic.expires_at <= datetime.now(UTC),
                )
                .distinct()
            )
        )
        for project_id in projects:
            await MessageDiagnosticRepository(session, project_id).expire_payloads()
        await session.commit()


async def diagnostic_retention_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await expire_message_diagnostics(session_factory)
        except Exception:
            logger.exception("message_diagnostic_retention_failed")
        await asyncio.sleep(60)
