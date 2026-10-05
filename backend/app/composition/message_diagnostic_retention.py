"""Application-owned expiry sweep; relational mutations remain project-scoped."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.evaluation_diagnostic import EvaluationDiagnostic
from app.models.message_diagnostic import MessageDiagnostic
from app.modules.conversations.repositories.message_diagnostic_repository import (
    MessageDiagnosticRepository,
)
from app.modules.evaluation.repositories.evaluation_diagnostic_repository import (
    EvaluationDiagnosticRepository,
)

logger = structlog.get_logger(__name__)


async def expire_message_diagnostics(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Sweep both diagnostic stores without requiring subsequent project traffic."""
    async with session_factory() as session:
        now = datetime.now(UTC)
        projects = list(
            await session.scalars(
                select(MessageDiagnostic.project_id)
                .where(
                    MessageDiagnostic.payload.is_not(None),
                    MessageDiagnostic.expires_at <= now,
                )
                .distinct()
            )
        )
        for project_id in projects:
            await MessageDiagnosticRepository(session, project_id).expire_payloads(now)
        evaluation_projects = list(
            await session.scalars(
                select(EvaluationDiagnostic.project_id)
                .where(
                    EvaluationDiagnostic.payload.is_not(None),
                    EvaluationDiagnostic.expires_at <= now,
                )
                .distinct()
            )
        )
        for project_id in evaluation_projects:
            await EvaluationDiagnosticRepository(session, project_id).expire_payloads(now)
        await session.commit()


async def diagnostic_retention_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await expire_message_diagnostics(session_factory)
        except Exception:
            logger.exception("diagnostic_retention_failed")
        await asyncio.sleep(60)
