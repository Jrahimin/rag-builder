"""Helpers for knowledge and retrieval integration tests."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.modules.knowledge.services.chunking.sentence_similarity_service import HashSentenceSimilarityService
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.modules.knowledge.workflows.document_processing import DocumentProcessingWorkflow
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX, DOCUMENT_PROCESS
from app.platform.providers.implementations.document_parser_factory import get_document_parser
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.platform.providers.implementations.vector_store_factory import create_vector_store_provider


async def _noop_ensure_project() -> None:
    return None


async def run_captured_document_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Execute captured document.process jobs on the test DB connection."""
    settings = get_settings()
    storage = create_storage_provider(settings)
    parser = get_document_parser()
    chunking = ChunkingService.from_settings(
        settings,
        similarity_service=HashSentenceSimilarityService(),
    )

    pending = list(jobs)
    jobs.clear()
    for job in pending:
        if job.name != DOCUMENT_PROCESS:
            jobs.append(job)
            continue
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            workflow = DocumentProcessingWorkflow(
                session=session,
                project_id=job.project_id,
                storage=storage,
                parser=parser,
                chunking=chunking,
            )
            await workflow.run(uuid.UUID(str(job.payload["document_id"])))


async def run_captured_embed_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Execute captured document.embed jobs on the test DB connection."""
    settings = get_settings()
    embedder = create_embedding_provider(settings)
    pending = list(jobs)
    jobs.clear()

    for job in pending:
        if job.name != DOCUMENT_EMBED:
            jobs.append(job)
            continue
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            service = IndexingService.from_settings(
                session=session,
                project_id=job.project_id,
                settings=settings,
                ensure_project=_noop_ensure_project,
                job_queue=get_job_queue(),
                embedder=embedder,
                vector_store=create_vector_store_provider(settings),
            )
            await service.run_embed(uuid.UUID(str(job.payload["document_id"])))


async def run_captured_index_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Execute captured document.index jobs on the test DB connection."""
    settings = get_settings()
    embedder = create_embedding_provider(settings)
    pending = list(jobs)
    jobs.clear()

    for job in pending:
        if job.name != DOCUMENT_INDEX:
            jobs.append(job)
            continue
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            service = IndexingService.from_settings(
                session=session,
                project_id=job.project_id,
                settings=settings,
                ensure_project=_noop_ensure_project,
                job_queue=get_job_queue(),
                embedder=embedder,
                vector_store=create_vector_store_provider(settings),
            )
            await service.run_index(uuid.UUID(str(job.payload["document_id"])))


async def run_captured_retrieval_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Run embed and index jobs inline for integration tests."""
    await run_captured_embed_jobs(connection, jobs)
    await run_captured_index_jobs(connection, jobs)


async def run_all_captured_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Process, embed, and index captured jobs in order."""
    await run_captured_document_jobs(connection, jobs)
    await run_captured_embed_jobs(connection, jobs)
    await run_captured_index_jobs(connection, jobs)
