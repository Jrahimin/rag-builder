"""HTTP-facing search orchestration."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AIConfigPolicy, RequestOverrideMode, RetrievalConfig, RetrievalStrategy
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever
from app.modules.retrieval.retrievers.models import RetrievalContext, RetrievalFilters
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.modules.retrieval.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse
from app.modules.retrieval.services.duplicate_suppression_service import (
    DuplicateSuppressionService,
)
from app.modules.retrieval.source_policy import (
    SourceMetadataReadPort,
    SourceMetadataScope,
    add_retrieval_provenance,
    apply_source_policy,
)
from app.platform.config.project_ai import SourcePolicyMode, cap_source_policy_mode
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
        ai_policy: AIConfigPolicy | None = None,
        source_metadata: SourceMetadataReadPort | None = None,
        configured_source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF,
        configuration_hash: str | None = None,
        config_provenance: dict[str, Any] | None = None,
        pinned_source_metadata_generation: int | None = None,
        pinned_index_build_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._reranker = reranker
        self._config = retrieval_config
        self._ai_policy = ai_policy or AIConfigPolicy()
        self._source_metadata = source_metadata
        self._configured_source_policy_mode = configured_source_policy_mode
        self._configuration_hash = configuration_hash
        self._config_provenance = config_provenance or {}
        self._pinned_source_metadata_generation = pinned_source_metadata_generation
        self._pinned_index_build_id = pinned_index_build_id
        self._hydrator = ResultHydrator(session, project_id)
        self._builds = IndexBuildRepository(session, project_id)
        self._duplicate_suppression = DuplicateSuppressionService(retrieval_config)

    async def search(self, request: SearchRequest) -> SearchResponse:
        started = time.perf_counter()
        top_k = min(
            request.top_k or self._config.default_top_k,
            self._ai_policy.max_request_top_k,
        )
        strategy = request.strategy or self._config.strategy
        if strategy not in self._ai_policy.enabled_retrieval_strategies:
            raise BadRequestError(
                message="The requested retrieval strategy is not enabled.",
                code="retrieval_strategy_not_enabled",
            )
        diagnostics: list[str] = []
        request_rerank = getattr(request, "rerank", None)
        if request_rerank is not None:
            diagnostics.append("rerank")
            if self._ai_policy.request_override_mode is RequestOverrideMode.STRICT:
                raise BadRequestError(
                    message="The request contains Project-owned AI policy overrides.",
                    code="request_policy_override_forbidden",
                    context={"fields": diagnostics},
                )
        rerank_enabled = (
            request_rerank if request_rerank is not None else self._config.rerank_enabled
        )
        active_build = (
            await self._builds.get_by_id(self._pinned_index_build_id)
            if self._pinned_index_build_id is not None
            else await self._builds.get_active()
        )
        source_scope, source_policy_status = await self._capture_source_scope(request.as_of)
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
                    compatibility_diagnostics=diagnostics,
                    as_of=request.as_of,
                    **self._source_diagnostics(
                        source_scope,
                        index_build_id=None,
                        status=source_policy_status,
                    ),
                ),
            )

        candidate_top_k = min(max(top_k * 2, top_k + 5), 100)
        if source_scope.effective_mode is SourcePolicyMode.ENFORCE:
            # Consolidation happens after ranking. Pull the full bounded
            # candidate window so lower-ranked distinct source groups can fill
            # the requested result count when higher ranks contain revisions
            # from the same logical source.
            candidate_top_k = 100
        context = RetrievalContext(
            project_id=self._project_id,
            query=request.query,
            index_build_id=active_build.id,
            embedding_set_version=active_build.embedding_set_version,
            filters=RetrievalFilters(
                document_id=request.document_id,
                metadata=dict(request.metadata_filter),
            ),
            top_k=candidate_top_k,
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
            source_scope=source_scope,
        )

        retriever = self._build_retriever(strategy)
        candidates = await retriever.retrieve(context)
        policy = apply_source_policy(candidates, mode=source_scope.effective_mode)
        candidates = add_retrieval_provenance(
            policy.candidates,
            index_build_id=active_build.id,
            source_scope=source_scope,
            configuration_hash=self._configuration_hash,
            config_provenance=self._config_provenance,
        )
        results = await self._hydrator.hydrate(candidates)
        suppression = self._duplicate_suppression.select(results, limit=top_k)
        results = suppression.results

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search_complete",
            project_id=str(self._project_id),
            duration_ms=elapsed_ms,
            hit_count=len(results),
            top_k=top_k,
            strategy=strategy.value,
            rerank_enabled=rerank_enabled,
            duplicate_suppression_removed=suppression.suppressed_count,
            duplicate_suppression_reasons=suppression.suppressed_by_reason,
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
                duplicate_suppression_input_count=suppression.input_count,
                duplicate_suppression_removed_count=suppression.suppressed_count,
                duplicate_suppression_reasons=suppression.suppressed_by_reason,
                compatibility_diagnostics=diagnostics,
                as_of=request.as_of,
                source_policy_consolidation_reasons=policy.consolidation_counts,
                **self._source_diagnostics(
                    source_scope,
                    index_build_id=active_build.id,
                    status=source_policy_status,
                ),
            ),
        )

    async def _capture_source_scope(
        self,
        as_of: datetime | None,
    ) -> tuple[SourceMetadataScope, str]:
        deployment_cap = self._ai_policy.source_policy_deployment_cap
        effective_mode = cap_source_policy_mode(
            self._configured_source_policy_mode,
            deployment_cap,
        )
        if effective_mode is SourcePolicyMode.OFF:
            return (
                SourceMetadataScope(
                    selectable=None,
                    generation=self._pinned_source_metadata_generation or 0,
                    configured_mode=self._configured_source_policy_mode,
                    effective_mode=SourcePolicyMode.OFF,
                    deployment_cap=deployment_cap.value,
                    reference_date=(as_of or datetime.now(UTC)).date().isoformat(),
                    explicit_as_of=as_of,
                ),
                "off",
            )
        if self._source_metadata is None:
            return (
                SourceMetadataScope(
                    selectable=None,
                    generation=self._pinned_source_metadata_generation or 0,
                    configured_mode=self._configured_source_policy_mode,
                    effective_mode=SourcePolicyMode.OFF,
                    deployment_cap=deployment_cap.value,
                    reference_date=(as_of or datetime.now(UTC)).date().isoformat(),
                    explicit_as_of=as_of,
                ),
                "unavailable_fallback_off",
            )
        try:
            scope = await self._source_metadata.capture(
                project_id=self._project_id,
                configured_mode=self._configured_source_policy_mode,
                deployment_cap=deployment_cap.value,
                as_of=as_of,
                generation=self._pinned_source_metadata_generation,
            )
        except SQLAlchemyError as exc:
            if effective_mode is SourcePolicyMode.ENFORCE:
                raise ServiceUnavailableError(
                    message="Source-aware retrieval is temporarily unavailable.",
                    code="source_policy_unavailable",
                ) from exc
            logger.warning(
                "source_policy_read_failed_falling_back",
                project_id=str(self._project_id),
                configured_mode=self._configured_source_policy_mode.value,
                effective_mode=effective_mode.value,
                error_type=type(exc).__name__,
            )
            return (
                SourceMetadataScope(
                    selectable=None,
                    generation=self._pinned_source_metadata_generation or 0,
                    configured_mode=self._configured_source_policy_mode,
                    effective_mode=SourcePolicyMode.OFF,
                    deployment_cap=deployment_cap.value,
                    reference_date=(as_of or datetime.now(UTC)).date().isoformat(),
                    explicit_as_of=as_of,
                ),
                "read_failed_fallback_off",
            )
        status = {
            SourcePolicyMode.OFF: "off",
            SourcePolicyMode.OBSERVE: "observed",
            SourcePolicyMode.ENFORCE: "enforced",
        }[scope.effective_mode]
        return scope, status

    def _source_diagnostics(
        self,
        scope: SourceMetadataScope,
        *,
        index_build_id: uuid.UUID | None,
        status: str,
    ) -> dict[str, Any]:
        return {
            "reference_date": scope.reference_date,
            "index_build_id": index_build_id,
            "source_metadata_generation": scope.generation,
            "source_policy_configured_mode": scope.configured_mode.value,
            "source_policy_effective_mode": scope.effective_mode.value,
            "source_policy_deployment_cap": scope.deployment_cap,
            "source_policy_status": status,
            "source_policy_exclusion_reasons": scope.exclusion_counts,
            "configuration_hash": self._configuration_hash,
            "config_provenance": dict(self._config_provenance),
        }

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
