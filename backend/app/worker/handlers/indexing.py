"""Taskiq handler for durable retrieval indexing jobs."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.retrieval import build_indexing_service
from app.core.config import Settings
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.errors import PermanentJobError
from app.worker.broker import broker
from app.worker.job_runtime import JobProgressReporter, run_durable_job

logger = structlog.get_logger(__name__)


async def _index(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    if run.document_id is None:
        raise PermanentJobError("Indexing job has no document reference.")
    service = build_indexing_service(
        session=session,
        project_id=run.project_id,
        settings=settings,
        job_submitter=jobs,
    )
    await service.run_index(
        run.document_id,
        expected_document_version=_document_version(run),
        on_progress=reporter.report,
    )
    return None


def _document_version(run: JobRun) -> int:
    value = run.payload.get("document_version")
    if not isinstance(value, int):
        raise PermanentJobError("Job payload has no valid document_version.")
    return value


async def run_document_index(
    *,
    project_id: uuid.UUID | str,
    job_id: uuid.UUID | str,
) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.DOCUMENT_INDEX,
        operation=_index,
    )


@broker.task(task_name=JobType.DOCUMENT_INDEX.value)
async def document_index_task(*, project_id: str, job_id: str) -> None:
    logger.info("taskiq_job_received", project_id=project_id, job_id=job_id)
    await run_document_index(project_id=project_id, job_id=job_id)
