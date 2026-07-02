"""Semantic vectorized retriever — vector search with chunk hydration."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.schemas.search import RetrievalResult
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.vector_store import (
    BaseVectorStoreProvider,
    VectorSearchFilter,
)
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


class SemanticRetriever:
    """Embed a query and return hydrated chunk hits from the vector store."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        *,
        default_top_k: int,
        score_threshold: float | None,
        filterable_metadata_keys: list[str],
        embedding_set_version: int,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._vector_store = vector_store
        self._default_top_k = default_top_k
        self._score_threshold = score_threshold
        self._filterable_metadata_keys = filterable_metadata_keys
        self._embedding_set_version = embedding_set_version
        self._chunk_repository = RetrievalChunkRepository(session, project_id)
        self._document_repository = RetrievalDocumentRepository(session, project_id)

    async def search(
        self,
        *,
        query: str,
        top_k: int | None = None,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[RetrievalResult]:
        started = time.perf_counter()
        effective_top_k = top_k or self._default_top_k
        allowed_filter = self._sanitize_metadata_filter(metadata_filter or {})

        try:
            embedded = await self._embedder.embed_texts([query])
            query_vector = embedded.vectors[0]
            hits = await self._vector_store.search(
                query_vector=query_vector,
                top_k=effective_top_k,
                filters=VectorSearchFilter(
                    project_id=self._project_id,
                    document_id=document_id,
                    embedding_set_version=self._embedding_set_version,
                    metadata=allowed_filter,
                ),
                score_threshold=self._score_threshold,
            )
        except ProviderError:
            raise

        chunk_ids = [uuid.UUID(hit.point_id) for hit in hits]
        chunks = await self._chunk_repository.map_by_ids(chunk_ids)
        documents = await self._document_repository.map_by_ids(
            {chunk.document_id for chunk in chunks.values()}
        )

        results: list[RetrievalResult] = []
        for hit in hits:
            chunk = chunks.get(uuid.UUID(hit.point_id))
            if chunk is None:
                continue
            document = documents.get(chunk.document_id)
            if document is None:
                continue
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=hit.score,
                    filename=document.filename,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata=dict(chunk.chunk_metadata),
                )
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search_complete",
            project_id=str(self._project_id),
            duration_ms=elapsed_ms,
            hit_count=len(results),
            top_k=effective_top_k,
        )
        return results

    def _sanitize_metadata_filter(self, metadata_filter: dict[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in metadata_filter.items()
            if key in self._filterable_metadata_keys
        }
