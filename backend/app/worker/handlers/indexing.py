"""Taskiq worker handlers for vector indexing."""

from __future__ import annotations

import uuid

import structlog

from app.core.config import get_settings
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.db.session import Database
from app.platform.jobs.names import DOCUMENT_INDEX
from app.worker.broker import broker

logger = structlog.get_logger(__name__)


async def _noop_ensure_project() -> None:
    return None


async def run_document_index(
    *,
    project_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
) -> None:
    """Run vector indexing outside the HTTP request path."""
    settings = get_settings()
    database = Database(settings)
    project_uuid = uuid.UUID(str(project_id))
    document_uuid = uuid.UUID(str(document_id))

    try:
        async with database.session_factory() as session:
            service = IndexingService.from_settings(
                session=session,
                project_id=project_uuid,
                settings=settings,
                ensure_project=_noop_ensure_project,
            )
            await service.run_index(document_uuid)
    finally:
        await database.dispose()


@broker.task(task_name=DOCUMENT_INDEX)
async def document_index_task(
    *,
    project_id: str,
    document_id: str,
) -> None:
    """Taskiq entrypoint for ``document.index`` jobs."""
    logger.info(
        "taskiq_document_index_received",
        project_id=project_id,
        document_id=document_id,
    )
    await run_document_index(project_id=project_id, document_id=document_id)
