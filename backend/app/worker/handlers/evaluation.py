"""Taskiq handler for reproducible evidence-quality evaluation runs."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.evaluation import build_evaluation_runner
from app.core.config import Settings
from app.models.evaluation_run import EvaluationRun
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.platform.config.project_ai import SourcePolicyMode
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
    evaluation_run = await session.get(EvaluationRun, evaluation_run_id)
    if evaluation_run is None or evaluation_run.project_id != run.project_id:
        raise PermanentJobError("Evaluation run was not found in the job Project.")
    configured_mode = evaluation_run.config_provenance.get(
        "configured_source_policy_mode",
        evaluation_run.config_snapshot.get("configuration", {}).get(
            "source_policy_mode", "off"
        ),
    )
    captured_configuration = evaluation_run.config_snapshot.get("configuration", {})
    if not isinstance(captured_configuration, dict):
        captured_configuration = {}
    runner = build_evaluation_runner(
        session=session,
        project_id=run.project_id,
        settings=settings,
        source_policy_mode=SourcePolicyMode(str(configured_mode)),
        source_metadata_generation=evaluation_run.source_metadata_generation,
        index_build_id=evaluation_run.index_build_id,
        configuration_hash=evaluation_run.config_snapshot.get("configuration_hash"),
        config_provenance=dict(evaluation_run.config_provenance),
        domain_instructions=str(captured_configuration.get("domain_instructions") or ""),
        prompt_profile=str(captured_configuration.get("prompt_profile") or "default"),
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
