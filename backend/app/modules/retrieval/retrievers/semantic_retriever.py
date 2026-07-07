"""Semantic vectorized retriever — returns candidate hits only."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalStrategy
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource, RetrievalContext
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.vector_store import (
    BaseVectorStoreProvider,
    VectorSearchFilter,
)
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


class SemanticRetriever(BaseRetriever):
    """Embed a query and return vector-store candidate hits."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._vector_store = vector_store

    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]:
        started = time.perf_counter()
        effective_top_k = (
            context.semantic_candidate_top_k
            if context.strategy is RetrievalStrategy.HYBRID
            else context.top_k
        )

        try:
            embedded = await self._embedder.embed_texts([context.query])
            query_vector = embedded.vectors[0]
            hits = await self._vector_store.search(
                query_vector=query_vector,
                top_k=effective_top_k,
                filters=VectorSearchFilter(
                    project_id=context.project_id,
                    document_id=context.filters.document_id,
                    embedding_set_version=context.embedding_set_version,
                    metadata=context.sanitized_metadata_filter(),
                ),
                score_threshold=context.score_threshold,
            )
        except ProviderError:
            raise

        candidates = [
            CandidateHit(
                chunk_id=uuid.UUID(hit.point_id),
                score=hit.score,
                source=CandidateSource.SEMANTIC,
                metadata=dict(hit.payload),
            )
            for hit in hits
        ]

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "semantic_retrieve_complete",
            project_id=str(context.project_id),
            duration_ms=elapsed_ms,
            candidate_count=len(candidates),
            top_k=effective_top_k,
        )
        return candidates
