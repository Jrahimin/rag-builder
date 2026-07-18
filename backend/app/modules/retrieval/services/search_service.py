"""HTTP-facing search orchestration."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalConfig, RetrievalStrategy
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever
from app.modules.retrieval.retrievers.models import RetrievalContext, RetrievalFilters
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.modules.retrieval.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider

logger = structlog.get_logger(__name__)


class SearchService:
    """Project-scoped search entry point with semantic and hybrid strategies."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        reranker: BaseRerankerProvider,
        retrieval_config: RetrievalConfig,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._reranker = reranker
        self._config = retrieval_config
        self._hydrator = ResultHydrator(session, project_id)
        self._builds = IndexBuildRepository(session, project_id)

    async def search(self, request: SearchRequest) -> SearchResponse:
        started = time.perf_counter()
        top_k = request.top_k or self._config.default_top_k
        strategy = request.strategy or self._config.strategy
        rerank_enabled = (
            request.rerank if request.rerank is not None else self._config.rerank_enabled
        )
        active_build = await self._builds.get_active()
        if active_build is None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return SearchResponse(
                results=[],
                query=request.query,
                top_k=top_k,
                diagnostics=SearchDiagnostics(
                    strategy=strategy,
                    duration_ms=elapsed_ms,
                    rerank_requested=False,
                    rerank_status="empty_corpus",
                    reranker_provider=None,
                    reranker_model=None,
                    reranker_version=None,
                ),
            )

        context = RetrievalContext(
            project_id=self._project_id,
            query=request.query,
            index_build_id=active_build.id,
            embedding_set_version=active_build.embedding_set_version,
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
            hnsw_ef_search=self._config.hnsw_ef_search,
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
        rerank_metadata = results[0].metadata if results else {}
        rerank_status = str(
            rerank_metadata.get(
                "rerank_status",
                (
                    "disabled"
                    if not rerank_enabled or strategy is not RetrievalStrategy.HYBRID
                    else "empty"
                ),
            )
        )
        return SearchResponse(
            results=results,
            query=request.query,
            top_k=top_k,
            diagnostics=SearchDiagnostics(
                strategy=strategy,
                duration_ms=elapsed_ms,
                rerank_requested=rerank_enabled and strategy is RetrievalStrategy.HYBRID,
                rerank_status=rerank_status,
                reranker_provider=_optional_string(rerank_metadata.get("reranker_provider")),
                reranker_model=_optional_string(rerank_metadata.get("reranker_model")),
                reranker_version=_optional_string(rerank_metadata.get("reranker_version")),
            ),
        )

    def _build_retriever(self, strategy: RetrievalStrategy) -> BaseRetriever:
        if strategy is RetrievalStrategy.HYBRID:
            return HybridRetriever(
                self._session,
                self._project_id,
                self._embedder,
                self._reranker,
                fts_regconfig=self._config.fts_regconfig,
            )
        return SemanticRetriever(
            self._session,
            self._project_id,
            self._embedder,
        )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
