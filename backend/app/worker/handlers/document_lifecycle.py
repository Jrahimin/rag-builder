"""Durable reversible delete and irreversible purge handlers."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.index_build import IndexBuildOperation, IndexBuildState
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.knowledge.domain.document_storage_keys import iter_document_storage_keys
from app.modules.knowledge.repositories.document_chunk_repository import DocumentChunkRepository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.services.retrieval_cleanup_service import RetrievalCleanupService
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.errors import PermanentJobError
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.worker.broker import broker
from app.worker.handlers.corpus import execute_index_build
from app.worker.job_runtime import JobProgressReporter, run_durable_job

logger = structlog.get_logger(__name__)


async def _execute(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    reporter: JobProgressReporter,
    *,
    purge: bool,
) -> None:
    if run.document_id is None:
        raise PermanentJobError("Document lifecycle job has no document reference.")
    documents = DocumentRepository(session, run.project_id)
    document = await documents.get_by_id(run.document_id, include_deleted=True, for_update=True)
    if document is None:
        if purge:
            return
        raise PermanentJobError("Document no longer exists.", code="document_not_found")
    operation = IndexBuildOperation.PURGE if purge else IndexBuildOperation.DELETE
    await execute_index_build(
        session,
        run,
        settings,
        reporter,
        operation=operation,
        auto_activate_default=True,
    )
    if not purge:
        if document.deleted_at is None:
            from datetime import UTC, datetime

            document.deleted_at = datetime.now(UTC)
            document.deleted_by = run.id
        run.result = {**(run.result or {}), "document_id": str(document.id), "mode": "delete"}
        return

    await reporter.report("purging_relational_artifacts", 92)
    cleanup = RetrievalCleanupService(session, run.project_id)
    await cleanup.on_document_delete(document.id)
    await DocumentChunkRepository(session, run.project_id).delete_by_document(document.id)
    builds = IndexBuildRepository(session, run.project_id)
    pointer = await builds.get_pointer(for_update=True)
    for build in await builds.list_all():
        manifest = build.manifest.get("documents", [])
        contains = any(str(item.get("document_id")) == str(document.id) for item in manifest)
        if contains and build.state is not IndexBuildState.ACTIVE:
            build.state = IndexBuildState.SUPERSEDED
            if pointer is not None and pointer.previous_build_id == build.id:
                pointer.previous_build_id = None
    storage = create_storage_provider(settings)
    await reporter.report("purging_storage_artifacts", 96)
    for key in iter_document_storage_keys(document):
        await storage.delete(key)
    await storage.delete_document_tree(project_id=run.project_id, document_id=document.id)
    purged_id = document.id
    await session.delete(document)
    run.result = {**(run.result or {}), "document_id": str(purged_id), "mode": "purge"}
    await reporter.report("purged", 100)


async def _delete(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    await _execute(session, run, settings, reporter, purge=False)
    return None


async def _purge(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    await _execute(session, run, settings, reporter, purge=True)
    return None


@broker.task(task_name=JobType.DOCUMENT_DELETE.value)
async def document_delete_task(*, project_id: str, job_id: str) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.DOCUMENT_DELETE,
        operation=_delete,
    )


@broker.task(task_name=JobType.DOCUMENT_PURGE.value)
async def document_purge_task(*, project_id: str, job_id: str) -> None:
    await run_durable_job(
        project_id=project_id, job_id=job_id, expected_type=JobType.DOCUMENT_PURGE, operation=_purge
    )
