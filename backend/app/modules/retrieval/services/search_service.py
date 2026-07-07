"""HTTP-facing search orchestration."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalConfig, RetrievalStrategy
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever
from app.modules.retrieval.retrievers.models import RetrievalContext, RetrievalFilters
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.modules.retrieval.schemas.search import SearchRequest, SearchResponse
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider

logger = structlog.get_logger(__name__)

type EnsureProjectFn = Callable[[], Awaitable[None]]


class SearchService:
    """Project-scoped search entry point with semantic and hybrid strategies."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        reranker: BaseRerankerProvider,
        retrieval_config: RetrievalConfig,
        *,
        ensure_project: EnsureProjectFn,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._vector_store = vector_store
        self._reranker = reranker
        self._config = retrieval_config
        self._ensure_project = ensure_project
        self._hydrator = ResultHydrator(session, project_id)

    async def search(self, request: SearchRequest) -> SearchResponse:
        await self._ensure_project()
        started = time.perf_counter()
        top_k = request.top_k or self._config.default_top_k
        strategy = request.strategy or self._config.strategy
        rerank_enabled = (
            request.rerank if request.rerank is not None else self._config.rerank_enabled
        )

        context = RetrievalContext(
            project_id=self._project_id,
            query=request.query,
            embedding_set_version=self._config.embedding_set_version,
            filters=RetrievalFilters(
                document_id=request.document_id,
                metadata=dict(request.metadata_filter),
            ),
            top_k=top_k,
            strategy=strategy,
            semantic_candidate_top_k=self._config.semantic_candidate_top_k,
            keyword_candidate_top_k=self._config.keyword_candidate_top_k,
            rrf_k=self._config.rrf_k,
            semantic_weight=self._config.semantic_weight,
            keyword_weight=self._config.keyword_weight,
            rerank_enabled=rerank_enabled,
            rerank_top_n=self._config.rerank_top_n,
            rerank_score_threshold=self._config.rerank_score_threshold,
            score_threshold=self._config.score_threshold,
            filterable_metadata_keys=tuple(self._config.filterable_metadata_keys),
            fts_regconfig=self._config.fts_regconfig,
            min_ocr_confidence=self._config.min_ocr_confidence,
            metadata={"request_strategy": strategy.value},
        )

        retriever = self._build_retriever(strategy)
        candidates = await retriever.retrieve(context)
        results = await self._hydrator.hydrate(candidates)
        results = results[:top_k]

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search_complete",
            project_id=str(self._project_id),
            duration_ms=elapsed_ms,
            hit_count=len(results),
            top_k=top_k,
            strategy=strategy.value,
            rerank_enabled=rerank_enabled,
        )
        return SearchResponse(results=results, query=request.query, top_k=top_k)

    def _build_retriever(self, strategy: RetrievalStrategy) -> BaseRetriever:
        if strategy is RetrievalStrategy.HYBRID:
            return HybridRetriever(
                self._session,
                self._project_id,
                self._embedder,
                self._vector_store,
                self._reranker,
                fts_regconfig=self._config.fts_regconfig,
            )
        return SemanticRetriever(
            self._session,
            self._project_id,
            self._embedder,
            self._vector_store,
        )
