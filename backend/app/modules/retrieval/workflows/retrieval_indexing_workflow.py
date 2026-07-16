"""Retrieval indexing finalization using PostgreSQL-native artifacts."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.chunk_embedding_repository import (
    ChunkEmbeddingRepository,
)
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.workflows.keyword_indexing_workflow import (
    KeywordIndexingWorkflow,
)
from app.modules.retrieval.workflows.stage_runner import StageFailure, run_document_stage
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider

logger = structlog.get_logger(__name__)


class RetrievalIndexingWorkflow:
    """Validate native vectors, rebuild keyword rows, and mark a document ready."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        *,
        embedding_set_version: int,
        filterable_metadata_keys: list[str],
        fts_regconfig: str = "simple",
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._embedding_set_version = embedding_set_version
        self._filterable_metadata_keys = filterable_metadata_keys
        self._fts_regconfig = fts_regconfig
        self._document_repository = RetrievalDocumentRepository(session, project_id)
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)

    async def run(self, document_id: uuid.UUID) -> Document | None:
        return await run_document_stage(
            session=self._session,
            repository=self._document_repository,
            project_id=self._project_id,
            document_id=document_id,
            stage="indexing",
            allowed_statuses={DocumentStatus.INDEXING, DocumentStatus.EMBEDDED},
            failure_message="Retrieval indexing failed.",
            work=self._index,
        )

    async def _index(self, document: Document) -> None:
        started = time.perf_counter()
        embeddings = await self._embedding_repository.list_by_document(
            document.id,
            embedding_set_version=self._embedding_set_version,
            provider=self._embedder.provider_name,
            model=self._embedder.model_name,
        )
        if not embeddings:
            raise StageFailure("No embeddings available for indexing.")

        keyword_workflow = KeywordIndexingWorkflow(
            session=self._session,
            project_id=self._project_id,
            embedding_set_version=self._embedding_set_version,
            filterable_metadata_keys=self._filterable_metadata_keys,
            fts_regconfig=self._fts_regconfig,
        )
        keyword_count = await keyword_workflow.index_document(document)
        if keyword_count != len(embeddings):
            raise StageFailure(
                "Embedding and keyword chunk counts differ; re-embed the document."
            )

        document.status = DocumentStatus.READY
        document.error_message = None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "retrieval_indexing_complete",
            project_id=str(self._project_id),
            document_id=str(document.id),
            duration_ms=elapsed_ms,
            embedding_count=len(embeddings),
            keyword_count=keyword_count,
        )
