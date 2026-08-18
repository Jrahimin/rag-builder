"""Semantic vectorized retriever — returns candidate hits only."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalStrategy
from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.models import CandidateHit, RetrievalContext
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SemanticRetrievalBatch:
    """Semantic candidates plus the query embedding provenance used to score them."""

    hits: list[CandidateHit]
    query_vector: list[float]
    provider: str
    model: str


class SemanticRetriever(BaseRetriever):
    """Embed a query and return vector-store candidate hits."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        repository: ChunkEmbeddingRepository | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._repository = repository or ChunkEmbeddingRepository(session, project_id)

    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]:
        return (await self.retrieve_batch(context)).hits

    async def retrieve_batch(
        self,
        context: RetrievalContext,
        *,
        query: str | None = None,
        language_scope: object | None = None,
        record_semantic_score: bool = True,
    ) -> SemanticRetrievalBatch:
        """Retrieve candidates and retain the query vector for hybrid score backfill."""
        started = time.perf_counter()
        effective_top_k = (
            context.semantic_candidate_top_k
            if context.strategy is RetrievalStrategy.HYBRID
            else context.top_k
        )
        query_text = query or context.query
        scope = language_scope if language_scope is not None else context.language_scope

        try:
            embedded = await self._embedder.embed_texts([query_text])
            query_vector = embedded.vectors[0]
            candidates = await self._repository.search_cosine(
                query_vector=query_vector,
                top_k=effective_top_k,
                index_build_id=context.index_build_id,
                document_id=context.filters.document_id,
                embedding_set_version=context.embedding_set_version,
                provider=embedded.provider,
                model=embedded.model,
                metadata_filter=context.sanitized_metadata_filter(),
                score_threshold=context.score_threshold,
                hnsw_ef_search=context.hnsw_ef_search,
                source_scope=context.source_scope,
                language_scope=scope,  # type: ignore[arg-type]
            )
        except ProviderError:
            raise

        if not record_semantic_score:
            candidates = [
                replace(
                    candidate,
                    semantic_score=None,
                    metadata={
                        **candidate.metadata,
                        "translated_dense_score": candidate.score,
                    },
                )
                for candidate in candidates
            ]

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "semantic_retrieve_complete",
            project_id=str(context.project_id),
            duration_ms=elapsed_ms,
            candidate_count=len(candidates),
            top_k=effective_top_k,
        )
        return SemanticRetrievalBatch(
            hits=candidates,
            query_vector=query_vector,
            provider=embedded.provider,
            model=embedded.model,
        )

    async def score_chunk_ids(
        self,
        context: RetrievalContext,
        chunk_ids: list[uuid.UUID],
        *,
        query_vector: list[float],
        provider: str,
        model: str,
    ) -> dict[uuid.UUID, float]:
        return await self._repository.score_chunk_ids(
            chunk_ids,
            query_vector=query_vector,
            index_build_id=context.index_build_id,
            embedding_set_version=context.embedding_set_version,
            provider=provider,
            model=model,
        )
