"""Helpers that execute captured Taskiq deliveries through durable job state."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import get_settings
from app.models.document import DocumentStatus
from app.models.job_run import JobType
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.platform.jobs.configuration import apply_job_configuration
from app.platform.jobs.contracts import JobConfiguration, JobDefinition, JobQueue
from app.platform.jobs.failure import classify_job_failure
from app.platform.jobs.names import (
    CORPUS_REEMBED,
    CORPUS_REINDEX,
    DOCUMENT_DELETE,
    DOCUMENT_EMBED,
    DOCUMENT_INDEX,
    DOCUMENT_PROCESS,
    DOCUMENT_PURGE,
    STORAGE_RECONCILE,
)
from app.worker.handlers.corpus import _reembed, _reindex
from app.worker.handlers.document import _process
from app.worker.handlers.document_lifecycle import _delete, _purge
from app.worker.handlers.embedding import _embed
from app.worker.handlers.evaluation import _evaluate
from app.worker.handlers.indexing import _index
from app.worker.handlers.storage_reconciliation import _reconcile


class _CaptureQueue(JobQueue):
    def __init__(self, jobs: list[JobDefinition]) -> None:
        self._jobs = jobs

    async def enqueue(self, job: JobDefinition) -> str:
        self._jobs.append(job)
        return job.idempotency_key or str(uuid.uuid4())


class _ProgressReporter:
    async def report(self, stage: str, progress: int) -> None:
        del stage, progress


async def _execute(
    connection: AsyncConnection,
    delivery: JobDefinition,
    captured_jobs: list[JobDefinition],
) -> None:
    settings = get_settings()
    project_id = delivery.project_id
    job_id = uuid.UUID(str(delivery.payload["job_id"]))
    queue = _CaptureQueue(captured_jobs)
    worker_id = f"integration:{uuid.uuid4()}"
    async with AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        service = build_job_service(
            session=session,
            project_id=project_id,
            settings=settings,
            queue=queue,
        )
        run = await service.acquire(job_id, worker_id=worker_id)
        if run is None:
            return
        detail = await service.get_detail(run.id)
        snapshot = JobConfiguration.model_validate(detail.configuration.configuration)
        effective = apply_job_configuration(settings, snapshot)
        reporter = _ProgressReporter()
        try:
            if run.job_type is JobType.DOCUMENT_PROCESS:
                child = await _process(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.DOCUMENT_EMBED:
                child = await _embed(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.DOCUMENT_INDEX:
                child = await _index(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.EVALUATION_RUN:
                child = await _evaluate(
                    session,
                    run,
                    effective,
                    service,
                    reporter,  # type: ignore[arg-type]
                )
            elif run.job_type is JobType.CORPUS_REEMBED:
                child = await _reembed(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.CORPUS_REINDEX:
                child = await _reindex(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.DOCUMENT_DELETE:
                child = await _delete(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.DOCUMENT_PURGE:
                child = await _purge(session, run, effective, service, reporter)  # type: ignore[arg-type]
            elif run.job_type is JobType.STORAGE_RECONCILE:
                child = await _reconcile(session, run, effective, service, reporter)  # type: ignore[arg-type]
            else:  # pragma: no cover - helper only dispatches the supported test jobs
                raise AssertionError(f"Unsupported captured job type: {run.job_type.value}")
            submission = await service.stage_success(
                run.id,
                worker_id=worker_id,
                child=child,
            )
            if submission is not None:
                await service.dispatch(submission.job_id)
        except Exception as exc:
            await session.rollback()
            failure = classify_job_failure(exc)
            failed_run, will_retry = await service.stage_failure(
                job_id,
                worker_id=worker_id,
                failure=failure,
            )
            if not will_retry and failed_run.document_id is not None:
                documents = DocumentRepository(session, project_id)
                document = await documents.get_by_id(
                    failed_run.document_id,
                    include_deleted=True,
                )
                if document is not None:
                    document.status = DocumentStatus.FAILED
                    document.error_message = failure.message
            await session.commit()


async def _run_named(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
    name: str,
) -> None:
    pending = list(jobs)
    jobs.clear()
    for delivery in pending:
        if delivery.name != name:
            jobs.append(delivery)
            continue
        await _execute(connection, delivery, jobs)


async def run_captured_document_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await _run_named(connection, jobs, DOCUMENT_PROCESS)


async def run_captured_embed_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await _run_named(connection, jobs, DOCUMENT_EMBED)


async def run_captured_index_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await _run_named(connection, jobs, DOCUMENT_INDEX)


async def run_captured_retrieval_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await run_captured_embed_jobs(connection, jobs)
    await run_captured_index_jobs(connection, jobs)


async def run_captured_evaluation_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await _run_named(connection, jobs, JobType.EVALUATION_RUN.value)


async def run_captured_lifecycle_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    for name in (
        CORPUS_REEMBED,
        CORPUS_REINDEX,
        DOCUMENT_DELETE,
        DOCUMENT_PURGE,
        STORAGE_RECONCILE,
    ):
        await _run_named(connection, jobs, name)


async def run_all_captured_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    await run_captured_document_jobs(connection, jobs)
    await run_captured_embed_jobs(connection, jobs)
    await run_captured_index_jobs(connection, jobs)
    await run_captured_lifecycle_jobs(connection, jobs)
    await run_captured_evaluation_jobs(connection, jobs)
