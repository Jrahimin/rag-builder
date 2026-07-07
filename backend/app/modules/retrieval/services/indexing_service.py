"""Indexing orchestration — embed/index enqueue, workflows, vector purge."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalConfig, Settings
from app.core.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError
from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.workflows.embedding_workflow import EmbeddingWorkflow
from app.modules.retrieval.workflows.vector_indexing_workflow import VectorIndexingWorkflow
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.jobs.names import DOCUMENT_EMBED, DOCUMENT_INDEX
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.implementations.vector_store_factory import get_vector_store_provider

logger = structlog.get_logger(__name__)

type EnsureProjectFn = Callable[[], Awaitable[None]]

_EMBED_ALLOWED = {
    DocumentStatus.CHUNKED,
    DocumentStatus.EMBEDDED,
    DocumentStatus.READY,
}
_INDEX_ALLOWED = {DocumentStatus.EMBEDDED, DocumentStatus.READY}


class IndexingService:
    """Orchestrates embedding and vector indexing within a Project."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        job_queue: JobQueue,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        retrieval_config: RetrievalConfig,
        *,
        embedding_batch_size: int,
        filterable_metadata_keys: list[str],
        ensure_project: EnsureProjectFn,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._job_queue = job_queue
        self._embedder = embedder
        self._vector_store = vector_store
        self._config = retrieval_config
        self._embedding_batch_size = embedding_batch_size
        self._filterable_metadata_keys = filterable_metadata_keys
        self._ensure_project = ensure_project
        self._document_repository = RetrievalDocumentRepository(session, project_id)
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)

    @classmethod
    def from_settings(
        cls,
        session: AsyncSession,
        project_id: uuid.UUID,
        settings: Settings,
        *,
        ensure_project: EnsureProjectFn,
        job_queue: JobQueue | None = None,
        embedder: BaseEmbeddingProvider | None = None,
        vector_store: BaseVectorStoreProvider | None = None,
    ) -> IndexingService:
        """Build a fully wired service from settings.

        Defaults to the process-scoped provider singletons; explicit overrides
        exist for tests and callers that manage provider lifecycles themselves.
        """
        return cls(
            session=session,
            project_id=project_id,
            job_queue=job_queue if job_queue is not None else get_job_queue(),
            embedder=embedder if embedder is not None else get_embedding_provider(),
            vector_store=(
                vector_store if vector_store is not None else get_vector_store_provider()
            ),
            retrieval_config=settings.retrieval,
            embedding_batch_size=settings.embedding.batch_size,
            filterable_metadata_keys=settings.retrieval.filterable_metadata_keys,
            ensure_project=ensure_project,
        )

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
        await self._ensure_project()
        document = await self._get_document_or_raise(document_id)
        self._require_status(document, _EMBED_ALLOWED, action="embed")
        previous_status = document.status

        document.status = DocumentStatus.EMBEDDING
        document = await flush_commit_refresh(
            self._session,
            self._document_repository,
            document,
        )

        try:
            await self._job_queue.enqueue(
                JobDefinition(
                    name=DOCUMENT_EMBED,
                    project_id=document.project_id,
                    payload={"document_id": str(document.id)},
                    idempotency_key=self._embed_idempotency_key(document),
                )
            )
        except JobEnqueueError as exc:
            document.status = previous_status
            await flush_commit_refresh(self._session, self._document_repository, document)
            raise ServiceUnavailableError(
                message="Background processing queue is unavailable.",
                code="job_queue_unavailable",
            ) from exc

        refreshed = await self._document_repository.get_by_id(document.id, include_deleted=True)
        return refreshed if refreshed is not None else document

    async def enqueue_index(self, document_id: uuid.UUID) -> Document:
        await self._ensure_project()
        document = await self._get_document_or_raise(document_id)
        self._require_status(document, _INDEX_ALLOWED, action="index")
        previous_status = document.status

        document.status = DocumentStatus.INDEXING
        document = await flush_commit_refresh(
            self._session,
            self._document_repository,
            document,
        )

        try:
            await self._job_queue.enqueue(
                JobDefinition(
                    name=DOCUMENT_INDEX,
                    project_id=document.project_id,
                    payload={"document_id": str(document.id)},
                    idempotency_key=self._index_idempotency_key(document),
                )
            )
        except JobEnqueueError as exc:
            document.status = previous_status
            await flush_commit_refresh(self._session, self._document_repository, document)
            raise ServiceUnavailableError(
                message="Background processing queue is unavailable.",
                code="job_queue_unavailable",
            ) from exc

        refreshed = await self._document_repository.get_by_id(document.id, include_deleted=True)
        return refreshed if refreshed is not None else document

    async def run_embed(self, document_id: uuid.UUID) -> Document | None:
        workflow = EmbeddingWorkflow(
            session=self._session,
            project_id=self._project_id,
            embedder=self._embedder,
            embedding_set_version=self._config.embedding_set_version,
            batch_size=self._embedding_batch_size,
        )
        document = await workflow.run(document_id)
        if document is not None and document.status is DocumentStatus.EMBEDDED:
            await self.enqueue_index_if_enabled(document.id)
        return document

    async def run_index(self, document_id: uuid.UUID) -> Document | None:
        workflow = VectorIndexingWorkflow(
            session=self._session,
            project_id=self._project_id,
            embedder=self._embedder,
            vector_store=self._vector_store,
            embedding_set_version=self._config.embedding_set_version,
            filterable_metadata_keys=self._filterable_metadata_keys,
            fts_regconfig=self._config.fts_regconfig,
        )
        return await workflow.run(document_id)

    def _embed_idempotency_key(self, document: Document) -> str:
        return (
            f"document.embed:{document.project_id}:{document.id}:"
            f"esv{self._config.embedding_set_version}"
        )

    def _index_idempotency_key(self, document: Document) -> str:
        return (
            f"document.index:{document.project_id}:{document.id}:"
            f"esv{self._config.embedding_set_version}"
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
