"""Shared skeleton for retrieval document-stage workflows.

Both retrieval workflows (embed, index) follow the same shape: load the
document, guard on allowed statuses, run stage work, and persist a terminal
status in a single commit. Only the middle "work" differs, so it is injected.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


class StageFailure(Exception):
    """Domain-level stage failure with a client-safe message."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


async def run_document_stage(
    *,
    session: AsyncSession,
    repository: RetrievalDocumentRepository,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    stage: str,
    allowed_statuses: set[DocumentStatus],
    failure_message: str,
    work: Callable[[Document], Awaitable[None]],
) -> Document | None:
    """Run one pipeline stage for a document with guards and failure handling.

    ``work`` must set the success status on the document; any raised
    ``StageFailure`` / ``ProviderError`` surfaces its message, other exceptions
    fall back to ``failure_message``. The terminal state is persisted here in a
    single commit.
    """
    document = await repository.get_by_id(document_id)
    if document is None:
        logger.warning(
            f"{stage}_skipped_missing",
            project_id=str(project_id),
            document_id=str(document_id),
        )
        return None

    if document.status not in allowed_statuses:
        logger.info(
            f"{stage}_skipped_status",
            project_id=str(project_id),
            document_id=str(document_id),
            status=document.status.value,
        )
        return document

    try:
        await work(document)
    except (StageFailure, ProviderError) as exc:
        document.status = DocumentStatus.FAILED
        document.error_message = exc.message
        logger.exception(
            f"{stage}_failed",
            project_id=str(project_id),
            document_id=str(document_id),
        )
    except Exception:
        document.status = DocumentStatus.FAILED
        document.error_message = failure_message
        logger.exception(
            f"{stage}_failed",
            project_id=str(project_id),
            document_id=str(document_id),
        )

    return await flush_commit_refresh(session, repository, document)
