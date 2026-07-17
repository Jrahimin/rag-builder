"""Taskiq handler for durable document processing jobs."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.retrieval import build_indexing_service
from app.core.config import EmbeddingBackend, Settings
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.knowledge.services.chunking.sentence_similarity_service import (
    HashSentenceSimilarityService,
    SentenceSimilarityService,
)
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.modules.knowledge.workflows.document_processing import DocumentProcessingWorkflow
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.errors import PermanentJobError
from app.platform.providers.implementations.document_parser_factory import create_document_parser
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.worker.broker import broker
from app.worker.job_runtime import JobProgressReporter, run_durable_job

logger = structlog.get_logger(__name__)


async def _process(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    if run.document_id is None:
        raise PermanentJobError("Document processing job has no document reference.")
    embedder = create_embedding_provider(settings)
    chunking = ChunkingService.from_settings(
        settings,
        similarity_service=(
            HashSentenceSimilarityService()
            if settings.embedding.backend is EmbeddingBackend.HASH
            else SentenceSimilarityService(embedder)
        ),
    )
    workflow = DocumentProcessingWorkflow(
        session=session,
        project_id=run.project_id,
        storage=create_storage_provider(settings),
        parser=create_document_parser(settings),
        chunking=chunking,
    )
    document = await workflow.run(
        run.document_id,
        expected_document_version=_document_version(run),
        on_progress=reporter.report,
    )
    if document is None or not settings.retrieval.auto_embed:
        return None
    indexing = build_indexing_service(
        session=session,
        project_id=run.project_id,
        settings=settings,
        job_submitter=jobs,
        embedder=embedder,
    )
    return indexing.build_embed_job(document)


def _document_version(run: JobRun) -> int:
    value = run.payload.get("document_version")
    if not isinstance(value, int):
        raise PermanentJobError("Job payload has no valid document_version.")
    return value


async def run_document_process(
    *,
    project_id: uuid.UUID | str,
    job_id: uuid.UUID | str,
) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.DOCUMENT_PROCESS,
        operation=_process,
    )


@broker.task(task_name=JobType.DOCUMENT_PROCESS.value)
async def document_process_task(*, project_id: str, job_id: str) -> None:
    logger.info("taskiq_job_received", project_id=project_id, job_id=job_id)
    await run_document_process(project_id=project_id, job_id=job_id)
