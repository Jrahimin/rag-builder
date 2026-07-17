"""Replay-safe shared skeleton for retrieval document stages."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.platform.db.advisory_lock import acquire_document_stage_lock
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.jobs.errors import PermanentJobError

logger = structlog.get_logger(__name__)


class StageFailure(PermanentJobError):
    """Stable domain-level stage failure."""

    code = "document_stage_failed"


async def run_document_stage(
    *,
    session: AsyncSession,
    repository: RetrievalDocumentRepository,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    stage: str,
    running_status: DocumentStatus,
    expected_document_version: int | None,
    work: Callable[[Document], Awaitable[None]],
) -> Document | None:
    """Fence and execute one idempotent stage; durable job state owns retries."""
    await acquire_document_stage_lock(
        session,
        project_id=project_id,
        document_id=document_id,
        stage=stage,
    )
    document = await repository.get_by_id(document_id, for_update=True)
    if document is None:
        logger.warning(
            f"{stage}_skipped_missing",
            project_id=str(project_id),
            document_id=str(document_id),
        )
        return None
    if expected_document_version is not None and document.version != expected_document_version:
        raise PermanentJobError(
            "Document version no longer matches this job.",
            context={
                "expected_document_version": expected_document_version,
                "actual_document_version": document.version,
            },
        )

    document.status = running_status
    document.error_message = None
    await work(document)
    return await flush_commit_refresh(session, repository, document)
