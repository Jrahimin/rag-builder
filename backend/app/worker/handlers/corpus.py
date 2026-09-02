"""Durable handlers for full isolated corpus index builds."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.workflows.index_build_workflow import IndexBuildWorkflow
from app.platform.jobs.configuration import embedding_set_version_from_configuration
from app.platform.jobs.contracts import JobConfiguration, JobDefinition
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
    embedding_set_version = _embedding_set_version_for_build(run, settings)
    raw_build_id = run.payload.get("build_id")
    if raw_build_id is None:
        snapshot = await session.get(JobConfigurationSnapshot, run.configuration_snapshot_id)
        if snapshot is None:
            raise PermanentJobError(
                "Job configuration snapshot does not exist.",
                code="job_configuration_snapshot_missing",
            )
        staged_configuration = JobConfiguration.model_validate(snapshot.configuration)
        embedding_set_version = (
            embedding_set_version_from_configuration(staged_configuration) or embedding_set_version
        )
        build = IndexBuild(
            project_id=run.project_id,
            job_id=run.id,
            operation=operation,
            state=IndexBuildState.BUILDING,
            embedding_set_version=embedding_set_version,
            configuration_hash=staged_configuration.index_output_digest(),
            artifact_fingerprint_version=(
                staged_configuration.index_artifact.fingerprint_version
                if staged_configuration.index_artifact is not None
                else None
            ),
            artifact_fingerprint=(
                staged_configuration.index_output_digest()
                if staged_configuration.index_artifact is not None
                else None
            ),
            index_profile_id=(
                (
                    staged_configuration.index_artifact.index_profile_id
                    or "legacy-unprofiled"
                )
                if staged_configuration.index_artifact is not None
                else "legacy-unprofiled"
            ),
            index_profile_hash=(
                staged_configuration.index_artifact.index_profile_hash
                if staged_configuration.index_artifact is not None
                else None
            ),
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
        desired_version = await _desired_embedding_set_version(session, run)
        if (
            build.state is IndexBuildState.BUILDING
            and desired_version is not None
            and desired_version != build.embedding_set_version
        ):
            build.embedding_set_version = desired_version

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


def _positive_embedding_set_version(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _embedding_set_version_for_build(run: JobRun, settings: Settings) -> int:
    return (
        _positive_embedding_set_version(run.payload.get("embedding_set_version"))
        or settings.retrieval.embedding_set_version
    )


async def _desired_embedding_set_version(session: AsyncSession, run: JobRun) -> int | None:
    """Resolve esv for an already-stamped BUILDING row from payload, then snapshot.

    Live process settings are not used here: a worker that loaded a newer env
    must not rewrite an immutable corpus build that was enqueued at esv=2.
    """
    payload_version = _positive_embedding_set_version(run.payload.get("embedding_set_version"))
    if payload_version is not None:
        return payload_version
    if run.configuration_snapshot_id is None:
        return None
    snapshot = await session.get(JobConfigurationSnapshot, run.configuration_snapshot_id)
    if snapshot is None:
        return None
    return embedding_set_version_from_configuration(
        JobConfiguration.model_validate(snapshot.configuration)
    )


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
