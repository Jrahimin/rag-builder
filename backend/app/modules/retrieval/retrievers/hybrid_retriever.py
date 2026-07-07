"""Hybrid retrieval orchestrator — RRF fusion and optional reranking."""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.candidate_content_loader import CandidateContentLoader
from app.modules.retrieval.retrievers.keyword_retriever import KeywordRetriever
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource, RetrievalContext
from app.modules.retrieval.retrievers.rrf_fusion import RankedList, reciprocal_rank_fusion
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankCandidate,
    RerankRequest,
)
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


class HybridRetriever(BaseRetriever):
    """Run semantic + keyword retrieval, fuse with RRF, optionally rerank."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        reranker: BaseRerankerProvider,
        *,
        fts_regconfig: str = "simple",
    ) -> None:
        self._semantic = SemanticRetriever(session, project_id, embedder, vector_store)
        self._keyword = KeywordRetriever(session, project_id, fts_regconfig=fts_regconfig)
        self._content_loader = CandidateContentLoader(session, project_id)
        self._reranker = reranker

    @classmethod
    def from_context(
        cls,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        reranker: BaseRerankerProvider,
        *,
        fts_regconfig: str = "simple",
    ) -> HybridRetriever:
        return cls(
            session,
            project_id,
            embedder,
            vector_store,
            reranker,
            fts_regconfig=fts_regconfig,
        )

    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]:
        started = time.perf_counter()
        semantic_hits, keyword_hits = await asyncio.gather(
            self._semantic.retrieve(context),
            self._keyword.retrieve(context),
        )

        fusion_top_k = (
            max(context.rerank_top_n, context.top_k) if context.rerank_enabled else context.top_k
        )
        fused = reciprocal_rank_fusion(
            [
                RankedList(hits=semantic_hits, weight=context.semantic_weight),
                RankedList(hits=keyword_hits, weight=context.keyword_weight),
            ],
            rrf_k=context.rrf_k,
            top_k=fusion_top_k,
        )

        final_candidates = fused
        if context.rerank_enabled and fused:
            final_candidates = await self._rerank_candidates(context, fused)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "hybrid_retrieve_complete",
            project_id=str(context.project_id),
            duration_ms=elapsed_ms,
            semantic_candidates=len(semantic_hits),
            keyword_candidates=len(keyword_hits),
            fused_candidates=len(fused),
            final_candidates=len(final_candidates),
        )
        return final_candidates[: context.top_k]

    async def _rerank_candidates(
        self,
        context: RetrievalContext,
        fused: list[CandidateHit],
    ) -> list[CandidateHit]:
        rerank_window = fused[: context.rerank_top_n]
        texts = await self._content_loader.load_texts(
            [candidate.chunk_id for candidate in rerank_window]
        )
        request = RerankRequest(
            query=context.query,
            candidates=[
                RerankCandidate(
                    chunk_id=candidate.chunk_id,
                    text=texts.get(candidate.chunk_id, ""),
                    source_score=candidate.score,
                    metadata=dict(candidate.metadata),
                )
                for candidate in rerank_window
                if candidate.chunk_id in texts
            ],
            top_n=context.rerank_top_n,
            metadata=dict(context.metadata),
        )
        if not request.candidates:
            return fused

        try:
            response = await self._reranker.rerank(request)
        except ProviderError:
            logger.warning(
                "rerank_failed_using_fused_order",
                project_id=str(context.project_id),
            )
            return fused

        reranked: list[CandidateHit] = []
        for result in response.results:
            if (
                context.rerank_score_threshold is not None
                and result.score < context.rerank_score_threshold
            ):
                continue
            reranked.append(
                CandidateHit(
                    chunk_id=result.chunk_id,
                    score=result.score,
                    source=CandidateSource.RERANK,
                    metadata=dict(result.metadata),
                )
            )

        if not reranked:
            return fused
        return reranked
