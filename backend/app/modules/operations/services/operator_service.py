"""Admin-gated deployment operator read model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import EmbeddingBackend, LLMBackend, Settings
from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.modules.operations.repositories.operator_repository import (
    OperatorRepository,
    age_seconds,
    last_24_hours,
)
from app.modules.operations.schemas.operator import (
    ActiveConfiguration,
    AuditEventResponse,
    ConfigurationSnapshotSummary,
    CorpusMetrics,
    DependencyOverview,
    JobMetrics,
    LatencyMetric,
    MetricsSnapshot,
    OperatorOverview,
    ProviderConfiguration,
    RecentFailure,
    TokenUsageMetrics,
    WorkerOverview,
    WorkerStatus,
)
from app.platform.jobs.worker_registry import WorkerRegistry
from app.platform.system.health_service import HealthService
from app.platform.system.schemas import PreflightStatus

log = get_logger(__name__)


class OperatorService:
    """Compose sanitized runtime, dependency, worker, and persistence signals."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: OperatorRepository,
        health: HealthService,
        worker_registry: WorkerRegistry,
        preflight: PreflightStatus,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._health = health
        self._worker_registry = worker_registry
        self._preflight = preflight

    async def dependencies(self) -> DependencyOverview:
        return DependencyOverview(
            readiness=await self._health.readiness(),
            startup_profile=self._preflight.profile,
            startup_checked_at=self._preflight.checked_at,
        )

    async def workers(self) -> WorkerOverview:
        try:
            heartbeats = await self._worker_registry.list()
        except Exception as exc:
            log.warning("operator_worker_registry_unavailable", error_type=type(exc).__name__)
            return WorkerOverview(
                available=False,
                active_count=0,
                stale_after_seconds=self._settings.runtime.worker_stale_seconds,
                workers=[],
                detail="Worker registry is unavailable; verify Redis connectivity.",
            )

        now = datetime.now(UTC)
        workers: list[WorkerStatus] = []
        for heartbeat in heartbeats:
            heartbeat_at = datetime.fromisoformat(heartbeat.heartbeat_at)
            started_at = datetime.fromisoformat(heartbeat.started_at)
            age = age_seconds(heartbeat_at, now=now) or 0.0
            workers.append(
                WorkerStatus(
                    worker_id=heartbeat.worker_id,
                    hostname=heartbeat.hostname,
                    process_id=heartbeat.process_id,
                    queue=heartbeat.queue,
                    version=heartbeat.version,
                    started_at=started_at,
                    heartbeat_at=heartbeat_at,
                    heartbeat_age_seconds=age,
                    state=(
                        "active" if age <= self._settings.runtime.worker_stale_seconds else "stale"
                    ),
                )
            )
        active_count = sum(worker.state == "active" for worker in workers)
        return WorkerOverview(
            available=True,
            active_count=active_count,
            stale_after_seconds=self._settings.runtime.worker_stale_seconds,
            workers=workers,
        )

    async def metrics(self) -> MetricsSnapshot:
        try:
            states = await self._repository.job_state_counts()
            retry_attempts = await self._repository.job_retry_attempts()
            failures_24h = await self._repository.failures_since(last_24_hours())
            oldest_queue = await self._repository.oldest_job_queue_time()
            (
                pending_dispatches,
                oldest_dispatch,
                dispatch_attempts,
            ) = await self._repository.outbox_metrics()
            job_latency_rows = await self._repository.job_latency()
            retrieval = await self._repository.chat_latency("retrieval_ms")
            generation = await self._repository.chat_latency("generation_ms")
            provider_latency_rows = await self._repository.provider_generation_latency(
                self._settings.llm.backend.value
            )
            input_tokens, output_tokens = await self._repository.token_usage()
            projects, documents, chunks, storage_bytes = await self._repository.corpus_counts()
        except SQLAlchemyError as exc:
            self._raise_operator_database_unavailable(exc)

        total = sum(states.values())
        return MetricsSnapshot(
            generated_at=datetime.now(UTC),
            jobs=JobMetrics(
                total=total,
                by_state=states,
                queued=states.get("queued", 0),
                running=states.get("running", 0),
                retry_scheduled=states.get("retry_scheduled", 0),
                failures_24h=failures_24h,
                retry_attempts=retry_attempts,
                oldest_queue_age_seconds=age_seconds(oldest_queue),
                pending_dispatches=pending_dispatches,
                oldest_dispatch_age_seconds=age_seconds(oldest_dispatch),
                dispatch_attempts=dispatch_attempts,
            ),
            job_latency=[
                LatencyMetric(name=name, count=count, average_ms=average, maximum_ms=maximum)
                for name, count, average, maximum in job_latency_rows
            ],
            provider_generation_latency=[
                LatencyMetric(name=name, count=count, average_ms=average, maximum_ms=maximum)
                for name, count, average, maximum in provider_latency_rows
            ],
            retrieval_latency=LatencyMetric(
                name="retrieval",
                count=retrieval[0],
                average_ms=retrieval[1],
                maximum_ms=retrieval[2],
            ),
            generation_latency=LatencyMetric(
                name="generation",
                count=generation[0],
                average_ms=generation[1],
                maximum_ms=generation[2],
            ),
            token_usage=TokenUsageMetrics(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            corpus=CorpusMetrics(
                projects=projects,
                documents=documents,
                chunks=chunks,
                storage_bytes=storage_bytes,
            ),
            active_embedding_set_version=self._settings.retrieval.embedding_set_version,
        )

    async def active_configuration(self) -> ActiveConfiguration:
        try:
            rows = await self._repository.recent_configuration_snapshots()
        except SQLAlchemyError as exc:
            self._raise_operator_database_unavailable(exc)
        latest_by_project: dict[uuid.UUID, JobConfigurationSnapshot] = {}
        for row in rows:
            latest_by_project.setdefault(row.project_id, row)
        snapshots = [
            ConfigurationSnapshotSummary(
                project_id=row.project_id,
                snapshot_id=row.id,
                configuration_hash=row.configuration_hash,
                schema_version=row.schema_version,
                created_at=row.created_at,
            )
            for row in latest_by_project.values()
        ]
        return ActiveConfiguration(
            environment=self._settings.app.env.value,
            runtime_profile=self._settings.runtime.profile.value,
            application_version=self._settings.app.version,
            llm=ProviderConfiguration(
                backend=self._settings.llm.backend.value,
                model=self._settings.llm.model,
                provider_version=self._settings.llm.provider_version,
                credential_configured=_llm_credential_configured(self._settings),
            ),
            embedding=ProviderConfiguration(
                backend=self._settings.embedding.backend.value,
                model=self._settings.embedding.model,
                dimensions=self._settings.embedding.dimensions,
                provider_version=self._settings.embedding.provider_version,
                credential_configured=_embedding_credential_configured(self._settings),
            ),
            reranker_backend=self._settings.retrieval.reranker_backend.value,
            ocr_backend=self._settings.ocr.backend.value,
            ocr_enabled=self._settings.ocr.enabled,
            storage_backend=self._settings.storage.backend.value,
            job_backend=self._settings.jobs.backend.value,
            retrieval_strategy=self._settings.retrieval.strategy.value,
            embedding_set_version=self._settings.retrieval.embedding_set_version,
            recent_project_snapshots=snapshots,
        )

    async def recent_failures(self, *, limit: int) -> list[RecentFailure]:
        try:
            rows = await self._repository.recent_failures(limit=limit)
        except SQLAlchemyError as exc:
            self._raise_operator_database_unavailable(exc)
        return [
            RecentFailure(
                job_id=row.id,
                project_id=row.project_id,
                document_id=row.document_id,
                job_type=row.job_type.value,
                stage=row.stage,
                failure_code=row.failure_code or "job_execution_failed",
                failure_message=row.failure_message or "Job execution failed.",
                attempt_count=row.attempt_count,
                failed_at=row.completed_at or row.updated_at,
            )
            for row in rows
        ]

    async def audit_events(self, *, limit: int, offset: int) -> list[AuditEventResponse]:
        try:
            rows = await self._repository.recent_audit_events(limit=limit, offset=offset)
        except SQLAlchemyError as exc:
            self._raise_operator_database_unavailable(exc)
        return [AuditEventResponse.model_validate(row) for row in rows]

    async def overview(self) -> OperatorOverview:
        dependencies = await self.dependencies()
        workers = await self.workers()
        metrics = await self.metrics()
        failures = await self.recent_failures(limit=10)
        healthy = dependencies.readiness.status == "ready" and workers.active_count > 0
        return OperatorOverview(
            status="ready" if healthy else "degraded",
            dependencies=dependencies,
            workers=workers,
            metrics=metrics,
            recent_failures=failures,
        )

    @staticmethod
    def _raise_operator_database_unavailable(exc: SQLAlchemyError) -> None:
        raise ServiceUnavailableError(
            message="Operational data is temporarily unavailable.",
            code="operator_data_unavailable",
            context={"error_type": type(exc).__name__},
        ) from exc


def _llm_credential_configured(settings: Settings) -> bool | None:
    if settings.llm.backend in {LLMBackend.OPENAI, LLMBackend.OPENAI_COMPATIBLE}:
        return bool(settings.llm.openai_api_key)
    if settings.llm.backend is LLMBackend.GEMINI:
        return bool(settings.llm.gemini_api_key)
    return None


def _embedding_credential_configured(settings: Settings) -> bool | None:
    if settings.embedding.backend is EmbeddingBackend.OPENAI:
        return bool(settings.embedding.openai_api_key)
    if settings.embedding.backend is EmbeddingBackend.GEMINI:
        return bool(settings.embedding.gemini_api_key)
    return None
