"""Shared durable lease/heartbeat/failure runtime for Taskiq handlers."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import Settings, get_settings
from app.models.document import DocumentStatus
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.platform.db.session import Database
from app.platform.jobs.configuration import apply_job_configuration
from app.platform.jobs.contracts import JobConfiguration, JobDefinition
from app.platform.jobs.errors import JobLeaseLostError, PermanentJobError
from app.platform.jobs.failure import classify_job_failure
from app.platform.jobs.implementations.job_queue_factory import create_job_queue

logger = structlog.get_logger(__name__)

type JobOperation = Callable[
    [AsyncSession, JobRun, Settings, JobService, "JobProgressReporter"],
    Awaitable[JobDefinition | None],
]


class JobProgressReporter:
    """Heartbeat/progress writer isolated from the long-running stage session."""

    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
        worker_id: str,
    ) -> None:
        self._database = database
        self._settings = settings
        self._project_id = project_id
        self._job_id = job_id
        self._worker_id = worker_id
        self._queue = create_job_queue(settings)

    async def report(self, stage: str, progress: int) -> None:
        if not await self._heartbeat(stage=stage, progress=progress):
            raise JobLeaseLostError("Job lease was lost while reporting progress.")

    async def heartbeat(self) -> bool:
        return await self._heartbeat()

    async def _heartbeat(
        self,
        *,
        stage: str | None = None,
        progress: int | None = None,
    ) -> bool:
        async with self._database.session_factory() as session:
            service = build_job_service(
                session=session,
                project_id=self._project_id,
                settings=self._settings,
                queue=self._queue,
            )
            return await service.heartbeat(
                self._job_id,
                worker_id=self._worker_id,
                stage=stage,
                progress=progress,
            )


async def run_durable_job(
    *,
    project_id: uuid.UUID | str,
    job_id: uuid.UUID | str,
    expected_type: JobType,
    operation: JobOperation,
) -> None:
    """Acquire one durable run and drive its terminal/retry transition."""
    settings = get_settings()
    database = Database(settings)
    project_uuid = uuid.UUID(str(project_id))
    job_uuid = uuid.UUID(str(job_id))
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    queue = create_job_queue(settings)

    try:
        async with database.session_factory() as session:
            service = build_job_service(
                session=session,
                project_id=project_uuid,
                settings=settings,
                queue=queue,
            )
            run = await service.acquire(job_uuid, worker_id=worker_id)
            if run is None:
                logger.info(
                    "job_delivery_ignored",
                    project_id=str(project_uuid),
                    job_id=str(job_uuid),
                )
                return
            reporter = JobProgressReporter(
                database=database,
                settings=settings,
                project_id=project_uuid,
                job_id=job_uuid,
                worker_id=worker_id,
            )
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(reporter, settings),
                name=f"job-heartbeat-{job_uuid}",
            )
            try:
                if run.job_type is not expected_type:
                    raise PermanentJobError(
                        "Task handler does not match the persisted job type.",
                        context={
                            "expected": expected_type.value,
                            "actual": run.job_type.value,
                        },
                    )
                detail = await service.get_detail(run.id)
                snapshot = JobConfiguration.model_validate(detail.configuration.configuration)
                effective_settings = apply_job_configuration(settings, snapshot)
                child = await operation(session, run, effective_settings, service, reporter)
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
                try:
                    failed_run, will_retry = await service.stage_failure(
                        run.id,
                        worker_id=worker_id,
                        failure=failure,
                    )
                except JobLeaseLostError:
                    logger.warning(
                        "stale_worker_result_discarded",
                        project_id=str(project_uuid),
                        job_id=str(job_uuid),
                    )
                    return
                if not will_retry and failed_run.document_id is not None:
                    documents = DocumentRepository(session, project_uuid)
                    document = await documents.get_by_id(
                        failed_run.document_id,
                        include_deleted=True,
                    )
                    expected_version = failed_run.payload.get("document_version")
                    if (
                        document is not None
                        and isinstance(expected_version, int)
                        and document.version == expected_version
                    ):
                        document.status = DocumentStatus.FAILED
                        document.error_message = failure.message
                await session.commit()
                logger.exception(
                    "durable_job_failed",
                    project_id=str(project_uuid),
                    job_id=str(job_uuid),
                    failure_code=failure.code,
                    retry_scheduled=will_retry,
                )
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
    finally:
        await database.dispose()


async def _heartbeat_loop(reporter: JobProgressReporter, settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.jobs.heartbeat_seconds)
        try:
            owned = await reporter.heartbeat()
        except Exception:
            logger.exception("job_heartbeat_failed")
            return
        if not owned:
            return
