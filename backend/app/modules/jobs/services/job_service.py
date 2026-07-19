"""Durable job submission, inspection, retry, leasing, and dispatch."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import JobsConfig
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.document import Document
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.models.job_run import JobRun, JobState
from app.modules.jobs.repositories.job_configuration_repository import (
    JobConfigurationRepository,
)
from app.modules.jobs.repositories.job_outbox_repository import JobOutboxRepository
from app.modules.jobs.repositories.job_run_repository import JobRunRepository
from app.modules.jobs.schemas.job import JobListParams
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.http.pagination import PaginatedResult
from app.platform.jobs.contracts import (
    DurableJobSubmitter,
    JobConfiguration,
    JobDefinition,
    JobQueue,
    JobSubmission,
    RetryPolicy,
)
from app.platform.jobs.errors import JobLeaseLostError
from app.platform.jobs.failure import JobFailure
from app.platform.webhooks.contracts import (
    NullWebhookEventPublisher,
    WebhookEventDefinition,
    WebhookEventPublisher,
    WebhookEventType,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JobDetail:
    run: JobRun
    configuration: JobConfigurationSnapshot


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    rescheduled: int
    failed: tuple[JobRun, ...]


class JobService(DurableJobSubmitter):
    """Project-scoped owner of durable job and outbox transitions."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        queue: JobQueue,
        config: JobsConfig,
        *,
        audit: AuditRecorder,
        webhooks: WebhookEventPublisher | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._queue = queue
        self._config = config
        self._audit = audit
        self._webhooks = webhooks or NullWebhookEventPublisher()
        self._runs = JobRunRepository(session, project_id)
        self._snapshots = JobConfigurationRepository(session, project_id)
        self._outbox = JobOutboxRepository(session, project_id)

    async def stage(
        self,
        job: JobDefinition,
        configuration: JobConfiguration,
        *,
        configuration_snapshot_id: uuid.UUID | None = None,
        retry_of_job_id: uuid.UUID | None = None,
    ) -> JobSubmission:
        if job.project_id != self._project_id:
            msg = "Job project_id does not match service scope"
            raise ValueError(msg)
        if configuration_snapshot_id is None:
            snapshot = await self._snapshots.get_or_create(configuration)
            configuration_snapshot_id = snapshot.id
        else:
            existing_snapshot = await self._snapshots.get(configuration_snapshot_id)
            if existing_snapshot is None:
                msg = "Configuration snapshot does not exist in this project"
                raise ValueError(msg)
        run, created = await self._runs.create_if_absent(
            job,
            configuration_snapshot_id=configuration_snapshot_id,
            retry_of_job_id=retry_of_job_id,
        )
        if created:
            self._outbox.add_intent(run.id)
            self._record_job_event(
                run,
                event_type=AuditEventType.JOB_SUBMITTED,
                actor_type=AuditActorType.SYSTEM,
                outcome=AuditOutcome.SUCCESS,
                detail={"job_type": run.job_type.value},
            )
        return JobSubmission(job_id=run.id, created=created)

    async def dispatch(self, job_id: uuid.UUID) -> None:
        """Attempt one pending intent and preserve it with backoff on failure."""
        await self._dispatch_claim(job_run_id=job_id)

    async def dispatch_next(self) -> bool:
        """Dispatch the next available project-scoped outbox row, if any."""
        return await self._dispatch_claim(job_run_id=None)

    async def _dispatch_claim(self, *, job_run_id: uuid.UUID | None) -> bool:
        outbox = await self._outbox.claim_pending(job_run_id=job_run_id)
        if outbox is None:
            # A concurrent dispatcher may already have claimed/completed this
            # intent. Close the read transaction without expiring caller-owned
            # ORM response objects (request sessions use expire_on_commit=False).
            await self._session.commit()
            return False
        run = await self._runs.get_by_id(outbox.job_run_id, include_deleted=True)
        if run is None:  # pragma: no cover - protected by FK
            await self._session.rollback()
            return False
        definition = JobDefinition(
            name=run.job_type.value,
            project_id=run.project_id,
            document_id=run.document_id,
            payload={"job_id": str(run.id)},
            idempotency_key=str(outbox.id),
            retry=RetryPolicy(max_attempts=1),
        )
        try:
            task_id = await self._queue.enqueue(definition)
        except Exception as exc:
            delay = min(
                self._config.dispatch_retry_base_seconds * (2 ** min(outbox.dispatch_attempts, 10)),
                self._config.dispatch_retry_max_seconds,
            )
            self._outbox.mark_dispatch_failed(
                outbox,
                error=f"{type(exc).__name__}: {exc}",
                available_at=datetime.now(UTC) + timedelta(seconds=delay),
            )
            self._record_job_event(
                run,
                event_type=AuditEventType.JOB_DISPATCH_DEFERRED,
                actor_type=AuditActorType.SYSTEM,
                outcome=AuditOutcome.DEFERRED,
                detail={
                    "dispatch_attempts": outbox.dispatch_attempts,
                    "error_type": type(exc).__name__,
                },
            )
            await self._session.commit()
            logger.warning(
                "job_dispatch_deferred",
                project_id=str(self._project_id),
                job_id=str(run.id),
                outbox_id=str(outbox.id),
                dispatch_attempts=outbox.dispatch_attempts,
                error_type=type(exc).__name__,
            )
            return True
        self._outbox.mark_dispatched(outbox, task_id=task_id)
        await self._session.commit()
        logger.info(
            "job_dispatched",
            project_id=str(self._project_id),
            job_id=str(run.id),
            outbox_id=str(outbox.id),
            task_id=task_id,
        )
        return True

    async def get(self, job_id: uuid.UUID) -> JobRun:
        run = await self._runs.get_by_id(job_id)
        if run is None:
            raise NotFoundError(message="Job not found.", code="job_not_found")
        return run

    async def get_detail(self, job_id: uuid.UUID) -> JobDetail:
        run = await self.get(job_id)
        snapshot = await self._snapshots.get(run.configuration_snapshot_id)
        if snapshot is None:  # pragma: no cover - protected by FK
            msg = "Job configuration snapshot is missing"
            raise RuntimeError(msg)
        return JobDetail(run=run, configuration=snapshot)

    async def list(self, params: JobListParams) -> PaginatedResult[JobRun]:
        items = await self._runs.list_filtered(
            limit=params.limit,
            offset=params.offset,
            state=params.state,
            job_type=params.job_type,
            document_id=params.document_id,
        )
        total = await self._runs.count_filtered(
            state=params.state,
            job_type=params.job_type,
            document_id=params.document_id,
        )
        return PaginatedResult(items=items, total=total, limit=params.limit, offset=params.offset)

    async def retry(self, job_id: uuid.UUID) -> JobRun:
        run = await self.get(job_id)
        if run.state is not JobState.FAILED:
            raise BadRequestError(
                message="Only failed jobs can be retried.",
                code="job_not_retryable",
            )
        snapshot = await self._snapshots.get(run.configuration_snapshot_id)
        if snapshot is None:  # pragma: no cover - protected by FK
            msg = "Job configuration snapshot is missing"
            raise RuntimeError(msg)
        definition = JobDefinition(
            name=run.job_type.value,
            project_id=run.project_id,
            document_id=run.document_id,
            payload=dict(run.payload),
            idempotency_key=f"job.retry:{run.id}:{uuid.uuid4()}",
            retry=RetryPolicy(max_attempts=run.max_attempts),
        )
        submission = await self.stage(
            definition,
            JobConfiguration.model_validate(snapshot.configuration),
            configuration_snapshot_id=snapshot.id,
            retry_of_job_id=run.id,
        )
        retried = await self._runs.get_by_id(submission.job_id, include_deleted=True)
        if retried is not None:
            self._record_job_event(
                retried,
                event_type=AuditEventType.JOB_RETRIED,
                actor_type=AuditActorType.OPERATOR,
                outcome=AuditOutcome.SUCCESS,
                detail={"retry_of_job_id": str(run.id)},
            )
        await self._session.commit()
        await self.dispatch(submission.job_id)
        return await self.get(submission.job_id)

    async def acquire(self, job_id: uuid.UUID, *, worker_id: str) -> JobRun | None:
        run = await self._runs.acquire(
            job_id,
            worker_id=worker_id,
            lease_seconds=self._config.lease_seconds,
        )
        if run is not None:
            self._record_job_event(
                run,
                event_type=AuditEventType.JOB_STARTED,
                actor_type=AuditActorType.WORKER,
                actor_id=worker_id,
                outcome=AuditOutcome.SUCCESS,
                detail={"attempt_count": run.attempt_count},
            )
        await self._session.commit()
        return run

    async def heartbeat(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        stage: str | None = None,
        progress: int | None = None,
    ) -> bool:
        owned = await self._runs.heartbeat(
            job_id,
            worker_id=worker_id,
            lease_seconds=self._config.lease_seconds,
            stage=stage,
            progress=progress,
        )
        await self._session.commit()
        return owned

    async def stage_success(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        child: JobDefinition | None = None,
    ) -> JobSubmission | None:
        run = await self._runs.lock_owned_run(job_id, worker_id=worker_id)
        if run is None:
            raise JobLeaseLostError("Job lease is no longer owned by this worker.")
        submission: JobSubmission | None = None
        if child is not None and await self._document_version_is_current(run):
            snapshot = await self._snapshots.get(run.configuration_snapshot_id)
            if snapshot is None:  # pragma: no cover - protected by FK
                msg = "Job configuration snapshot is missing"
                raise RuntimeError(msg)
            submission = await self.stage(
                child,
                JobConfiguration.model_validate(snapshot.configuration),
                configuration_snapshot_id=snapshot.id,
            )
        self._runs.mark_succeeded(run)
        await self._stage_webhook_outcome(run, succeeded=True)
        self._record_job_event(
            run,
            event_type=AuditEventType.JOB_SUCCEEDED,
            actor_type=AuditActorType.WORKER,
            actor_id=worker_id,
            outcome=AuditOutcome.SUCCESS,
            detail={"attempt_count": run.attempt_count},
        )
        await self._session.commit()
        return submission

    async def _document_version_is_current(self, run: JobRun) -> bool:
        """Fence child publication against a concurrently committed reprocess."""
        if run.document_id is None:
            return True
        expected_version = run.payload.get("document_version")
        if not isinstance(expected_version, int):
            return False
        current_version = await self._session.scalar(
            select(Document.version)
            .where(
                Document.id == run.document_id,
                Document.project_id == self._project_id,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return current_version == expected_version

    async def stage_failure(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        failure: JobFailure,
    ) -> tuple[JobRun, bool]:
        run = await self._runs.lock_owned_run(job_id, worker_id=worker_id)
        if run is None:
            raise JobLeaseLostError("Job lease is no longer owned by this worker.")
        will_retry = failure.retryable and run.attempt_count < run.max_attempts
        if will_retry:
            available_at = datetime.now(UTC) + timedelta(
                seconds=self._retry_delay(run.attempt_count)
            )
            self._runs.schedule_retry(
                run,
                available_at=available_at,
                code=failure.code,
                message=failure.message,
            )
            run.failure_details = {**failure.details, "retryable": True}
            self._outbox.add_intent(run.id, available_at=available_at)
            event_type = AuditEventType.JOB_RETRY_SCHEDULED
            outcome = AuditOutcome.DEFERRED
        else:
            self._runs.mark_failed(
                run,
                code=failure.code,
                message=failure.message,
                details={**failure.details, "retryable": failure.retryable},
            )
            event_type = AuditEventType.JOB_FAILED
            outcome = AuditOutcome.FAILURE
            await self._stage_webhook_outcome(run, succeeded=False)
        self._record_job_event(
            run,
            event_type=event_type,
            actor_type=AuditActorType.WORKER,
            actor_id=worker_id,
            outcome=outcome,
            detail={
                "attempt_count": run.attempt_count,
                "failure_code": failure.code,
                "retryable": failure.retryable,
            },
        )
        return run, will_retry

    async def recover_expired(self, *, limit: int) -> RecoveryResult:
        expired = await self._runs.list_expired_for_update(limit=limit)
        failed: list[JobRun] = []
        for run in expired:
            if run.attempt_count < run.max_attempts:
                available_at = datetime.now(UTC)
                self._runs.schedule_retry(
                    run,
                    available_at=available_at,
                    code="job_lease_expired",
                    message="The previous worker lease expired; execution was recovered.",
                )
                self._outbox.add_intent(run.id, available_at=available_at)
                self._record_job_event(
                    run,
                    event_type=AuditEventType.JOB_RECOVERED,
                    actor_type=AuditActorType.SYSTEM,
                    outcome=AuditOutcome.DEFERRED,
                    detail={"attempt_count": run.attempt_count},
                )
            else:
                self._runs.mark_failed(
                    run,
                    code="job_attempts_exhausted",
                    message="The worker lease expired and all attempts were exhausted.",
                    details={"retryable": True},
                )
                failed.append(run)
                await self._stage_webhook_outcome(run, succeeded=False)
                self._record_job_event(
                    run,
                    event_type=AuditEventType.JOB_FAILED,
                    actor_type=AuditActorType.SYSTEM,
                    outcome=AuditOutcome.FAILURE,
                    detail={
                        "attempt_count": run.attempt_count,
                        "failure_code": "job_attempts_exhausted",
                    },
                )
        return RecoveryResult(rescheduled=len(expired) - len(failed), failed=tuple(failed))

    def _retry_delay(self, attempt_count: int) -> float:
        return min(
            self._config.retry_base_delay_seconds * (2 ** max(attempt_count - 1, 0)),
            self._config.retry_max_delay_seconds,
        )

    def _record_job_event(
        self,
        run: JobRun,
        *,
        event_type: AuditEventType,
        actor_type: AuditActorType,
        outcome: AuditOutcome,
        actor_id: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        safe_detail = dict(detail or {})
        if run.document_id is not None:
            safe_detail["document_id"] = str(run.document_id)
        self._audit.record(
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="job_run",
            resource_id=run.id,
            outcome=outcome,
            detail=safe_detail,
        )

    async def _stage_webhook_outcome(self, run: JobRun, *, succeeded: bool) -> None:
        event_type: WebhookEventType | None = None
        if run.job_type.value == "document.process":
            event_type = (
                WebhookEventType.DOCUMENT_PROCESSING_SUCCEEDED_V1
                if succeeded
                else WebhookEventType.DOCUMENT_PROCESSING_FAILED_V1
            )
        elif run.job_type.value == "document.index":
            event_type = (
                WebhookEventType.DOCUMENT_INDEXING_SUCCEEDED_V1
                if succeeded
                else WebhookEventType.DOCUMENT_INDEXING_FAILED_V1
            )
        if event_type is None or run.document_id is None:
            return
        data: dict[str, object] = {
            "job_id": str(run.id),
            "document_id": str(run.document_id),
            "document_version": run.payload.get("document_version"),
            "outcome": "succeeded" if succeeded else "failed",
        }
        if not succeeded:
            data["failure_code"] = run.failure_code
        build_id = run.payload.get("build_id")
        if build_id is not None:
            data["build_id"] = str(build_id)
        await self._webhooks.stage(
            WebhookEventDefinition(
                event_type=event_type,
                source_key=f"job:{run.id}:{'succeeded' if succeeded else 'failed'}",
                source_type="job_run",
                source_id=run.id,
                data=data,
                occurred_at=datetime.now(UTC),
            )
        )
