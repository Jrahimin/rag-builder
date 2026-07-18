"""Durable handlers for full isolated corpus index builds."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.workflows.index_build_workflow import IndexBuildWorkflow
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.errors import PermanentJobError
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.worker.broker import broker
from app.worker.job_runtime import JobProgressReporter, run_durable_job

logger = structlog.get_logger(__name__)


async def execute_index_build(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    reporter: JobProgressReporter,
    *,
    operation: IndexBuildOperation,
    auto_activate_default: bool,
) -> IndexBuild:
    repository = IndexBuildRepository(session, run.project_id)
    raw_build_id = run.payload.get("build_id")
    if raw_build_id is None:
        build = IndexBuild(
            project_id=run.project_id,
            job_id=run.id,
            operation=operation,
            state=IndexBuildState.BUILDING,
            embedding_set_version=settings.retrieval.embedding_set_version,
            configuration_hash=build_job_configuration(settings).digest(),
        )
        repository.add(build)
        await repository.flush()
        run.payload = {**run.payload, "build_id": str(build.id)}
    else:
        try:
            build_id = uuid.UUID(str(raw_build_id))
        except ValueError as exc:
            raise PermanentJobError(
                "Job payload has no valid build_id.", code="index_build_id_invalid"
            ) from exc
        existing_build = await repository.get_by_id(build_id, for_update=True)
        if existing_build is None:
            raise PermanentJobError("Index build does not exist.", code="index_build_not_found")
        build = existing_build

    workflow = IndexBuildWorkflow(
        session=session,
        project_id=run.project_id,
        embedder=create_embedding_provider(settings),
        embedding_set_version=build.embedding_set_version,
        batch_size=settings.embedding.batch_size,
        filterable_metadata_keys=settings.retrieval.filterable_metadata_keys,
        fts_regconfig=settings.retrieval.fts_regconfig,
        on_progress=reporter.report,
    )
    result = await workflow.run(
        build.id,
        exclude_document_id=_optional_uuid(run.payload.get("exclude_document_id")),
        auto_activate=bool(run.payload.get("auto_activate", auto_activate_default)),
    )
    run.result = {
        "build_id": str(result.id),
        "state": result.state.value,
        "document_count": result.document_count,
        "chunk_count": result.chunk_count,
        "corpus_fingerprint": result.corpus_fingerprint,
    }
    return result


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise PermanentJobError(
            "Job payload contains an invalid document reference.",
            code="document_id_invalid",
        ) from exc


async def _reembed(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    await execute_index_build(
        session,
        run,
        settings,
        reporter,
        operation=IndexBuildOperation.REEMBED,
        auto_activate_default=False,
    )
    return None


async def _reindex(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    await execute_index_build(
        session,
        run,
        settings,
        reporter,
        operation=IndexBuildOperation.REINDEX,
        auto_activate_default=False,
    )
    return None


async def run_corpus_reembed(*, project_id: uuid.UUID | str, job_id: uuid.UUID | str) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.CORPUS_REEMBED,
        operation=_reembed,
    )


async def run_corpus_reindex(*, project_id: uuid.UUID | str, job_id: uuid.UUID | str) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.CORPUS_REINDEX,
        operation=_reindex,
    )


@broker.task(task_name=JobType.CORPUS_REEMBED.value)
async def corpus_reembed_task(*, project_id: str, job_id: str) -> None:
    logger.info("taskiq_job_received", project_id=project_id, job_id=job_id)
    await run_corpus_reembed(project_id=project_id, job_id=job_id)


@broker.task(task_name=JobType.CORPUS_REINDEX.value)
async def corpus_reindex_task(*, project_id: str, job_id: str) -> None:
    logger.info("taskiq_job_received", project_id=project_id, job_id=job_id)
    await run_corpus_reindex(project_id=project_id, job_id=job_id)
