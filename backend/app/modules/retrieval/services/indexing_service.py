"""Indexing orchestration — native embedding and retrieval-index jobs."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalConfig
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.platform.jobs.contracts import (
    DurableJobSubmitter,
    JobConfiguration,
    JobDefinition,
    RetryPolicy,
)
from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX

logger = structlog.get_logger(__name__)

_EMBED_ALLOWED = {
    DocumentStatus.CHUNKED,
    DocumentStatus.EMBEDDED,
    DocumentStatus.READY,
}
_INDEX_ALLOWED = {DocumentStatus.EMBEDDED, DocumentStatus.READY}


class IndexingService:
    """Orchestrates embedding and retrieval indexing within a Project."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        job_submitter: DurableJobSubmitter,
        job_configuration: JobConfiguration,
        retrieval_config: RetrievalConfig,
        *,
        job_max_attempts: int,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._job_submitter = job_submitter
        self._job_configuration = job_configuration
        self._job_max_attempts = job_max_attempts
        self._config = retrieval_config
        self._document_repository = RetrievalDocumentRepository(session, project_id)

    @property
    def embedding_set_version(self) -> int:
        return self._config.embedding_set_version

    async def enqueue_embed_if_enabled(self, document_id: uuid.UUID) -> Document | None:
        if not self._config.auto_embed:
            return await self._document_repository.get_by_id(document_id)
        return await self.enqueue_embed(document_id)

    async def enqueue_index_if_enabled(self, document_id: uuid.UUID) -> Document | None:
        if not self._config.auto_index:
            return await self._document_repository.get_by_id(document_id)
        return await self.enqueue_index(document_id)

    async def enqueue_embed(self, document_id: uuid.UUID) -> Document:
        document = await self._get_document_or_raise(document_id)
        self._require_status(document, _EMBED_ALLOWED, action="embed")
        submission = await self._job_submitter.stage(
            self.build_embed_job(document),
            self._job_configuration,
        )
        if submission.created:
            document.status = DocumentStatus.EMBEDDING
        await self._session.commit()
        await self._session.refresh(document)
        await self._job_submitter.dispatch(submission.job_id)
        document.__dict__["job_id"] = submission.job_id
        return document

    async def enqueue_index(self, document_id: uuid.UUID) -> Document:
        document = await self._get_document_or_raise(document_id)
        self._require_status(document, _INDEX_ALLOWED, action="index")
        submission = await self._job_submitter.stage(
            self.build_index_job(document),
            self._job_configuration,
        )
        if submission.created:
            document.status = DocumentStatus.INDEXING
        await self._session.commit()
        await self._session.refresh(document)
        await self._job_submitter.dispatch(submission.job_id)
        document.__dict__["job_id"] = submission.job_id
        return document

    def _embed_idempotency_key(self, document: Document) -> str:
        return (
            f"document.embed:{document.project_id}:{document.id}:"
            f"v{document.version}:esv{self._config.embedding_set_version}:"
            f"cfg{self._job_configuration.digest()[:16]}"
        )

    def _index_idempotency_key(self, document: Document) -> str:
        return (
            f"document.index:{document.project_id}:{document.id}:"
            f"v{document.version}:esv{self._config.embedding_set_version}:"
            f"cfg{self._job_configuration.digest()[:16]}"
        )

    def build_embed_job(self, document: Document) -> JobDefinition:
        return JobDefinition(
            name=DOCUMENT_EMBED,
            project_id=document.project_id,
            document_id=document.id,
            payload={
                "document_version": document.version,
                "embedding_set_version": self._config.embedding_set_version,
                "operation": "reembed",
            },
            idempotency_key=self._embed_idempotency_key(document),
            retry=RetryPolicy(max_attempts=self._job_max_attempts),
        )

    def build_index_job(self, document: Document) -> JobDefinition:
        return JobDefinition(
            name=DOCUMENT_INDEX,
            project_id=document.project_id,
            document_id=document.id,
            payload={
                "document_version": document.version,
                "embedding_set_version": self._config.embedding_set_version,
                "operation": "reindex",
            },
            idempotency_key=self._index_idempotency_key(document),
            retry=RetryPolicy(max_attempts=self._job_max_attempts),
        )

    async def _get_document_or_raise(self, document_id: uuid.UUID) -> Document:
        document = await self._document_repository.get_by_id(document_id)
        if document is None:
            raise NotFoundError(
                message="Document not found.",
                code="document_not_found",
            )
        return document

    @staticmethod
    def _require_status(
        document: Document,
        allowed: set[DocumentStatus],
        *,
        action: str,
    ) -> None:
        if document.status not in allowed:
            raise BadRequestError(
                message=f"Document cannot be {action} while status is {document.status.value}.",
                code=f"document_not_{action}able",
            )
