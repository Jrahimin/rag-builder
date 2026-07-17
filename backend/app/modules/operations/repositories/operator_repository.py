"""Read-only deployment-wide aggregates for the admin-gated operator backend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.models.job_outbox import JobOutbox, JobOutboxState
from app.models.job_run import JobRun, JobState
from app.models.message import Message, MessageRole
from app.models.project import Project


class OperatorRepository:
    """Deployment-wide, read-only queries reachable only through admin APIs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def job_state_counts(self) -> dict[str, int]:
        rows = await self._session.execute(
            select(JobRun.state, func.count(JobRun.id)).group_by(JobRun.state)
        )
        return {state.value: int(count) for state, count in rows.all()}

    async def job_retry_attempts(self) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.sum(func.greatest(JobRun.attempt_count - 1, 0)), 0))
        )
        return int(value or 0)

    async def failures_since(self, since: datetime) -> int:
        value = await self._session.scalar(
            select(func.count(JobRun.id)).where(
                JobRun.state == JobState.FAILED,
                JobRun.completed_at >= since,
            )
        )
        return int(value or 0)

    async def oldest_job_queue_time(self) -> datetime | None:
        return await self._session.scalar(
            select(func.min(JobRun.queued_at)).where(
                JobRun.state.in_([JobState.QUEUED, JobState.RETRY_SCHEDULED])
            )
        )

    async def outbox_metrics(self) -> tuple[int, datetime | None, int]:
        row = (
            await self._session.execute(
                select(
                    func.count(JobOutbox.id),
                    func.min(JobOutbox.available_at),
                    func.coalesce(func.sum(JobOutbox.dispatch_attempts), 0),
                ).where(JobOutbox.state == JobOutboxState.PENDING)
            )
        ).one()
        return int(row[0]), row[1], int(row[2])

    async def job_latency(self) -> list[tuple[str, int, float | None, float | None]]:
        duration = func.extract("epoch", JobRun.completed_at - JobRun.started_at) * 1000.0
        rows = await self._session.execute(
            select(
                JobRun.job_type,
                func.count(JobRun.id),
                func.avg(duration),
                func.max(duration),
            )
            .where(JobRun.started_at.is_not(None), JobRun.completed_at.is_not(None))
            .group_by(JobRun.job_type)
            .order_by(JobRun.job_type)
        )
        return [
            (job_type.value, int(count), _float_or_none(average), _float_or_none(maximum))
            for job_type, count, average, maximum in rows.all()
        ]

    async def chat_latency(
        self,
        metric_name: str,
    ) -> tuple[int, float | None, float | None]:
        value = cast(Message.message_metadata[metric_name].astext, Float)
        row = (
            await self._session.execute(
                select(func.count(value), func.avg(value), func.max(value)).where(
                    Message.role == MessageRole.ASSISTANT,
                    Message.message_metadata.has_key(metric_name),
                )
            )
        ).one()
        return int(row[0]), _float_or_none(row[1]), _float_or_none(row[2])

    async def provider_generation_latency(
        self,
        default_provider: str,
    ) -> list[tuple[str, int, float | None, float | None]]:
        value = cast(Message.message_metadata["generation_ms"].astext, Float)
        provider = func.coalesce(Message.provider, Conversation.provider, default_provider)
        rows = await self._session.execute(
            select(provider, func.count(value), func.avg(value), func.max(value))
            .join(
                Conversation,
                (Conversation.id == Message.conversation_id)
                & (Conversation.project_id == Message.project_id),
            )
            .where(
                Message.role == MessageRole.ASSISTANT,
                Message.message_metadata.has_key("generation_ms"),
            )
            .group_by(provider)
            .order_by(provider)
        )
        return [
            (str(name), int(count), _float_or_none(average), _float_or_none(maximum))
            for name, count, average, maximum in rows.all()
        ]

    async def token_usage(self) -> tuple[int, int]:
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(Message.input_tokens), 0),
                    func.coalesce(func.sum(Message.output_tokens), 0),
                ).where(Message.role == MessageRole.ASSISTANT)
            )
        ).one()
        return int(row[0]), int(row[1])

    async def corpus_counts(self) -> tuple[int, int, int, int]:
        projects = int(await self._session.scalar(select(func.count(Project.id))) or 0)
        document_row = (
            await self._session.execute(
                select(
                    func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)
                ).where(Document.deleted_at.is_(None))
            )
        ).one()
        chunks = int(await self._session.scalar(select(func.count(DocumentChunk.id))) or 0)
        return projects, int(document_row[0]), chunks, int(document_row[1])

    async def recent_failures(self, *, limit: int) -> list[JobRun]:
        rows = await self._session.execute(
            select(JobRun)
            .where(JobRun.state == JobState.FAILED)
            .order_by(JobRun.completed_at.desc(), JobRun.id.desc())
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def recent_audit_events(self, *, limit: int, offset: int) -> list[AuditEvent]:
        rows = await self._session.execute(
            select(AuditEvent)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def recent_configuration_snapshots(
        self, *, limit: int = 100
    ) -> list[JobConfigurationSnapshot]:
        rows = await self._session.execute(
            select(JobConfigurationSnapshot)
            .order_by(
                JobConfigurationSnapshot.created_at.desc(),
                JobConfigurationSnapshot.id.desc(),
            )
            .limit(limit)
        )
        return list(rows.scalars().all())


def age_seconds(value: datetime | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    current = now or datetime.now(UTC)
    return max(round((current - value).total_seconds(), 3), 0.0)


def _float_or_none(value: Any) -> float | None:
    return round(float(value), 3) if value is not None else None


def last_24_hours() -> datetime:
    return datetime.now(UTC) - timedelta(hours=24)
