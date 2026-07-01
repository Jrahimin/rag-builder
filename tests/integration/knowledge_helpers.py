"""Helpers for knowledge integration tests."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.modules.knowledge.workflows.document_processing import DocumentProcessingWorkflow
from app.platform.jobs.contracts import JobDefinition
from app.platform.providers.implementations.document_parser_factory import get_document_parser
from app.platform.providers.implementations.storage_factory import create_storage_provider


async def run_captured_document_jobs(
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> None:
    """Execute captured document.process jobs on the test DB connection."""
    settings = get_settings()
    storage = create_storage_provider(settings)
    parser = get_document_parser()
    chunking = ChunkingService(
        chunk_size=settings.chunking.chunk_size,
        chunk_overlap=settings.chunking.chunk_overlap,
    )

    while jobs:
        job = jobs.pop(0)
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            workflow = DocumentProcessingWorkflow(
                session=session,
                project_id=job.project_id,
                storage=storage,
                parser=parser,
                chunking=chunking,
            )
            await workflow.run(uuid.UUID(str(job.payload["document_id"])))
