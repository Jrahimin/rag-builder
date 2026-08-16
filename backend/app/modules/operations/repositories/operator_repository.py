"""Read-only deployment-wide aggregates for the admin-gated operator backend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, and_, case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CompoundSelect

from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.evaluation_run import EvaluationRun
from app.models.generation import Generation, GenerationStatus
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.models.job_outbox import JobOutbox, JobOutboxState
from app.models.job_run import JobRun, JobState
from app.models.message import Message, MessageRole
from app.models.organization import Organization
from app.models.project import Project
from app.modules.operations.schemas.operator import UsageBucket, UsageWorkload


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
        value = {
            "retrieval_ms": Message.retrieval_latency_ms,
            "generation_ms": Message.provider_latency_ms,
            "total_ms": Message.total_latency_ms,
        }[metric_name]
        row = (
            await self._session.execute(
                select(func.count(value), func.avg(value), func.max(value)).where(
                    Message.role == MessageRole.ASSISTANT,
                    value.is_not(None),
                )
            )
        ).one()
        return int(row[0]), _float_or_none(row[1]), _float_or_none(row[2])

    async def provider_generation_latency(
        self,
        default_provider: str,
    ) -> list[tuple[str, int, float | None, float | None]]:
        value = Message.provider_latency_ms
        provider = func.coalesce(Message.provider, Conversation.provider, default_provider)
        chat_rows = await self._session.execute(
            select(provider, func.count(value), func.avg(value), func.max(value))
            .join(
                Conversation,
                (Conversation.id == Message.conversation_id)
                & (Conversation.project_id == Message.project_id),
            )
            .where(
                Message.role == MessageRole.ASSISTANT,
                Message.provider_latency_ms.is_not(None),
            )
            .group_by(provider)
            .order_by(provider)
        )
        generation_rows = await self._session.execute(
            select(
                Generation.provider,
                func.count(Generation.provider_latency_ms),
                func.avg(Generation.provider_latency_ms),
                func.max(Generation.provider_latency_ms),
            )
            .where(Generation.provider_latency_ms.is_not(None))
            .group_by(Generation.provider)
            .order_by(Generation.provider)
        )
        aggregates: dict[str, tuple[int, float, float]] = {}
        for name, count, average, maximum in [*chat_rows.all(), *generation_rows.all()]:
            resolved_name = str(name)
            resolved_count = int(count)
            resolved_average = float(average or 0.0)
            resolved_maximum = float(maximum or 0.0)
            previous_count, previous_sum, previous_maximum = aggregates.get(
                resolved_name,
                (0, 0.0, 0.0),
            )
            aggregates[resolved_name] = (
                previous_count + resolved_count,
                previous_sum + (resolved_average * resolved_count),
                max(previous_maximum, resolved_maximum),
            )
        return [
            (
                name,
                count,
                round(total / count, 3) if count else None,
                round(maximum, 3) if count else None,
            )
            for name, (count, total, maximum) in sorted(aggregates.items())
        ]

    async def token_usage(self) -> tuple[int, int]:
        message_row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(Message.input_tokens), 0),
                    func.coalesce(func.sum(Message.output_tokens), 0),
                ).where(Message.role == MessageRole.ASSISTANT)
            )
        ).one()
        generation_row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(Generation.input_tokens), 0),
                    func.coalesce(func.sum(Generation.output_tokens), 0),
                )
            )
        ).one()
        return (
            int(message_row[0]) + int(generation_row[0]),
            int(message_row[1]) + int(generation_row[1]),
        )

    async def usage_aggregates(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        bucket: UsageBucket,
        organization_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
        workload: UsageWorkload | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        executions = _usage_execution_union().subquery("usage_executions")
        clauses = [
            executions.c.occurred_at >= start_at,
            executions.c.occurred_at < end_at,
        ]
        if organization_id is not None:
            clauses.append(Project.organization_id == organization_id)
        if project_id is not None:
            clauses.append(executions.c.project_id == project_id)
        if provider is not None:
            clauses.append(executions.c.provider == provider)
        if model is not None:
            clauses.append(executions.c.model == model)
        if workload is not None:
            clauses.append(executions.c.workload == workload.value)

        bucket_start = func.date_trunc(bucket.value, executions.c.occurred_at)
        dimensions = (
            bucket_start.label("bucket_start"),
            Project.organization_id.label("organization_id"),
            Organization.name.label("organization_name"),
            executions.c.project_id,
            Project.name.label("project_name"),
            executions.c.provider,
            executions.c.model,
            executions.c.workload,
        )
        grouped = (
            select(*dimensions, *_usage_aggregate_columns(executions))
            .select_from(executions)
            .join(Project, Project.id == executions.c.project_id)
            .join(Organization, Organization.id == Project.organization_id)
            .where(*clauses)
            .group_by(*dimensions)
            .order_by(bucket_start.desc(), Organization.name, Project.name)
        )
        total = (
            select(*_usage_aggregate_columns(executions))
            .select_from(executions)
            .join(Project, Project.id == executions.c.project_id)
            .join(Organization, Organization.id == Project.organization_id)
            .where(*clauses)
        )
        rows = await self._session.execute(grouped)
        total_row = (await self._session.execute(total)).mappings().one()
        return [dict(row) for row in rows.mappings().all()], dict(total_row)

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

    async def recent_audit_events(
        self,
        *,
        limit: int,
        offset: int,
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent)
        if organization_id is not None:
            stmt = stmt.where(AuditEvent.organization_id == organization_id)
        if project_id is not None:
            stmt = stmt.where(AuditEvent.project_id == project_id)
        rows = await self._session.execute(
            stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
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


def _usage_execution_union() -> CompoundSelect[Any]:
    chat = (
        select(
            Message.id.label("record_id"),
            Message.project_id,
            Message.created_at.label("occurred_at"),
            func.coalesce(Message.provider, Conversation.provider).label("provider"),
            func.coalesce(Message.model, Conversation.model).label("model"),
            literal(UsageWorkload.CHAT.value).label("workload"),
            Message.input_tokens,
            Message.output_tokens,
            Message.retrieval_latency_ms,
            Message.provider_latency_ms,
            Message.total_latency_ms,
            literal(0).label("error_count"),
        )
        .join(
            Conversation,
            and_(
                Conversation.id == Message.conversation_id,
                Conversation.project_id == Message.project_id,
            ),
        )
        .where(Message.role == MessageRole.ASSISTANT)
    )
    contextual = select(
        Generation.id.label("record_id"),
        Generation.project_id,
        Generation.created_at.label("occurred_at"),
        Generation.provider,
        Generation.model,
        literal(UsageWorkload.CONTEXTUAL_GENERATION.value).label("workload"),
        Generation.input_tokens,
        Generation.output_tokens,
        literal(None).cast(Integer).label("retrieval_latency_ms"),
        Generation.provider_latency_ms,
        Generation.total_latency_ms,
        case((Generation.status == GenerationStatus.FAILED, 1), else_=0).label(
            "error_count"
        ),
    )
    evaluation = (
        select(
            EvaluationRun.id.label("record_id"),
            EvaluationRun.project_id,
            EvaluationRun.created_at.label("occurred_at"),
            EvaluationRun.provider,
            EvaluationRun.model,
            literal(UsageWorkload.EVALUATION.value).label("workload"),
            EvaluationRun.input_tokens,
            EvaluationRun.output_tokens,
            EvaluationRun.retrieval_latency_ms,
            EvaluationRun.provider_latency_ms,
            EvaluationRun.total_latency_ms,
            case((JobRun.state == JobState.FAILED, 1), else_=0).label("error_count"),
        )
        .join(
            JobRun,
            and_(
                JobRun.id == EvaluationRun.job_id,
                JobRun.project_id == EvaluationRun.project_id,
            ),
        )
    )
    return union_all(chat, contextual, evaluation)


def _usage_aggregate_columns(executions: Any) -> tuple[Any, ...]:
    requests = func.count(executions.c.record_id)
    token_records = func.count(executions.c.input_tokens)
    input_tokens = case(
        (token_records == requests, func.sum(executions.c.input_tokens)),
        else_=None,
    ).label("input_tokens")
    output_records = func.count(executions.c.output_tokens)
    output_tokens = case(
        (output_records == requests, func.sum(executions.c.output_tokens)),
        else_=None,
    ).label("output_tokens")
    complete_token_records = func.count(
        case(
            (
                and_(
                    executions.c.input_tokens.is_not(None),
                    executions.c.output_tokens.is_not(None),
                ),
                1,
            )
        )
    )
    return (
        requests.label("request_count"),
        func.coalesce(func.sum(executions.c.error_count), 0).label("error_count"),
        complete_token_records.label("records_with_token_usage"),
        input_tokens,
        output_tokens,
        func.count(executions.c.retrieval_latency_ms).label("retrieval_samples"),
        func.avg(executions.c.retrieval_latency_ms).label("retrieval_average_ms"),
        func.max(executions.c.retrieval_latency_ms).label("retrieval_maximum_ms"),
        func.count(executions.c.provider_latency_ms).label("provider_samples"),
        func.avg(executions.c.provider_latency_ms).label("provider_average_ms"),
        func.max(executions.c.provider_latency_ms).label("provider_maximum_ms"),
        func.count(executions.c.total_latency_ms).label("total_samples"),
        func.avg(executions.c.total_latency_ms).label("total_average_ms"),
        func.max(executions.c.total_latency_ms).label("total_maximum_ms"),
    )
