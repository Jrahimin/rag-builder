"""Taskiq worker handlers for document processing."""

from __future__ import annotations

import uuid

import structlog

from app.core.config import get_settings
from app.models.document import DocumentStatus
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.modules.knowledge.workflows.document_processing import DocumentProcessingWorkflow
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.db.session import Database
from app.platform.jobs.names import DOCUMENT_PROCESS
from app.platform.providers.implementations.document_parser_factory import get_document_parser
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.worker.broker import broker

logger = structlog.get_logger(__name__)


async def _noop_ensure_project() -> None:
    return None


async def run_document_process(
    *,
    project_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
) -> None:
    """Run document parsing outside the HTTP request path."""
    settings = get_settings()
    database = Database(settings)
    project_uuid = uuid.UUID(str(project_id))
    document_uuid = uuid.UUID(str(document_id))
    chunking = ChunkingService.from_settings(settings)

    try:
        async with database.session_factory() as session:
            workflow = DocumentProcessingWorkflow(
                session=session,
                project_id=project_uuid,
                storage=create_storage_provider(settings),
                parser=get_document_parser(),
                chunking=chunking,
            )
            document = await workflow.run(document_uuid)
            if document is not None and document.status is DocumentStatus.CHUNKED:
                indexing = IndexingService.from_settings(
                    session=session,
                    project_id=project_uuid,
                    settings=settings,
                    ensure_project=_noop_ensure_project,
                )
                await indexing.enqueue_embed_if_enabled(document.id)
    finally:
        await database.dispose()


@broker.task(task_name=DOCUMENT_PROCESS)
async def document_process_task(
    *,
    project_id: str,
    document_id: str,
) -> None:
    """Taskiq entrypoint for ``document.process`` jobs."""
    logger.info(
        "taskiq_document_process_received",
        project_id=project_id,
        document_id=document_id,
    )
    await run_document_process(project_id=project_id, document_id=document_id)
