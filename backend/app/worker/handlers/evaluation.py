"""Taskiq handler for reproducible evidence-quality evaluation runs."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.evaluation import build_evaluation_runner
from app.core.config import Settings
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.errors import PermanentJobError
from app.worker.broker import broker
from app.worker.job_runtime import JobProgressReporter, run_durable_job

logger = structlog.get_logger(__name__)


async def _evaluate(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    value = run.payload.get("evaluation_run_id")
    try:
        evaluation_run_id = uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise PermanentJobError("Evaluation job has no valid evaluation_run_id.") from None
    runner = build_evaluation_runner(
        session=session,
        project_id=run.project_id,
        settings=settings,
    )
    await runner.run(evaluation_run_id, on_progress=reporter.report)
    return None


async def run_evaluation(
    *,
    project_id: uuid.UUID | str,
    job_id: uuid.UUID | str,
) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.EVALUATION_RUN,
        operation=_evaluate,
    )


@broker.task(task_name=JobType.EVALUATION_RUN.value)
async def evaluation_run_task(*, project_id: str, job_id: str) -> None:
    logger.info("taskiq_job_received", project_id=project_id, job_id=job_id)
    await run_evaluation(project_id=project_id, job_id=job_id)
