"""Vector indexing workflow — persist embeddings to the vector store."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.workflows.keyword_indexing_workflow import KeywordIndexingWorkflow
from app.modules.retrieval.workflows.stage_runner import StageFailure, run_document_stage
from app.platform.persistence.vector_codec import unpack_vector
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider, VectorPoint

logger = structlog.get_logger(__name__)


class VectorIndexingWorkflow:
    """Push PostgreSQL embeddings to the configured vector store."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        *,
        embedding_set_version: int,
        filterable_metadata_keys: list[str],
        fts_regconfig: str = "simple",
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._vector_store = vector_store
        self._embedding_set_version = embedding_set_version
        self._filterable_metadata_keys = filterable_metadata_keys
        self._fts_regconfig = fts_regconfig
        self._document_repository = RetrievalDocumentRepository(session, project_id)
        self._chunk_repository = RetrievalChunkRepository(session, project_id)
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)

    async def run(self, document_id: uuid.UUID) -> Document | None:
        return await run_document_stage(
            session=self._session,
            repository=self._document_repository,
            project_id=self._project_id,
            document_id=document_id,
            stage="indexing",
            allowed_statuses={DocumentStatus.INDEXING, DocumentStatus.EMBEDDED},
            failure_message="Vector indexing failed.",
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

        chunk_map = await self._chunk_repository.map_by_ids(
            [row.chunk_id for row in embeddings],
            document_id=document.id,
        )

        await self._vector_store.ensure_collection(dimensions=self._embedder.dimensions)
        await self._vector_store.delete_by_document(
            project_id=self._project_id,
            document_id=document.id,
        )
        points = [
            VectorPoint(
                point_id=str(embedding.chunk_id),
                vector=unpack_vector(embedding.vector, dimensions=embedding.dimensions),
                payload=self._build_payload(document, chunk_map.get(embedding.chunk_id)),
            )
            for embedding in embeddings
            if chunk_map.get(embedding.chunk_id) is not None
        ]
        await self._vector_store.upsert_points(points)

        keyword_workflow = KeywordIndexingWorkflow(
            session=self._session,
            project_id=self._project_id,
            embedding_set_version=self._embedding_set_version,
            filterable_metadata_keys=self._filterable_metadata_keys,
            fts_regconfig=self._fts_regconfig,
        )
        await keyword_workflow.index_document(document)

        document.status = DocumentStatus.READY
        document.error_message = None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "indexing_complete",
            project_id=str(self._project_id),
            document_id=str(document.id),
            duration_ms=elapsed_ms,
            point_count=len(points),
        )

    def _build_payload(
        self,
        document: Document,
        chunk: DocumentChunk | None,
    ) -> dict[str, object]:
        if chunk is None:
            return {
                "project_id": str(self._project_id),
                "document_id": str(document.id),
                "embedding_set_version": self._embedding_set_version,
            }
        metadata = {
            key: str(chunk.chunk_metadata[key])
            for key in self._filterable_metadata_keys
            if key in chunk.chunk_metadata
        }
        return {
            "project_id": str(self._project_id),
            "document_id": str(document.id),
            "chunk_index": chunk.chunk_index,
            "embedding_set_version": self._embedding_set_version,
            **metadata,
        }
