"""HTTP-facing search orchestration."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    AIConfigPolicy,
    QueryTranslationConfig,
    RequestOverrideMode,
    RerankMode,
    RetrievalConfig,
    RetrievalStrategy,
)
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.models.index_build import IndexBuild
from app.modules.retrieval.embedding_identity import (
    EmbeddingIdentity,
    QueryEmbedderFactory,
    identity_from_manifest,
    identity_from_vector_rows,
    incompatible_identity_error,
    unlabeled_identity_error,
)
from app.modules.retrieval.multilingual.translation import resolve_multilingual_plan
from app.modules.retrieval.repositories.chunk_embedding_repository import (
    ChunkEmbeddingRepository,
)
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever
from app.modules.retrieval.retrievers.models import RetrievalContext, RetrievalFilters
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.modules.retrieval.schemas.search import (
    RetrievalResult,
    SearchDiagnostics,
    SearchRequest,
    SearchResponse,
)
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
from app.platform.providers.contracts.query_translation import BaseQueryTranslationProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.errors import ProviderError

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
        query_translator: BaseQueryTranslationProvider | None = None,
        query_translation_config: QueryTranslationConfig | None = None,
        persist_translation_text: bool = False,
        query_embedder_factory: QueryEmbedderFactory | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._query_embedder_factory = query_embedder_factory
        self._reranker = reranker
        self._config = retrieval_config
        self._ai_policy = ai_policy or AIConfigPolicy()
        self._source_metadata = source_metadata
        self._configured_source_policy_mode = configured_source_policy_mode
        self._configuration_hash = configuration_hash
        self._config_provenance = config_provenance or {}
        self._pinned_source_metadata_generation = pinned_source_metadata_generation
        self._pinned_index_build_id = pinned_index_build_id
        self._query_translator = query_translator
        self._query_translation_config = query_translation_config or QueryTranslationConfig()
        self._persist_translation_text = persist_translation_text
        self._hydrator = ResultHydrator(session, project_id)
        self._builds = IndexBuildRepository(session, project_id)
        self._embeddings = ChunkEmbeddingRepository(session, project_id)
        self._duplicate_suppression = DuplicateSuppressionService(retrieval_config)
        self.resolved_query_embedder: BaseEmbeddingProvider | None = None

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
                    embedding_identity_status="empty_corpus",
                    embedding_provider=None,
                    embedding_model=None,
                    embedding_dimensions=None,
                    embedding_set_version=None,
                    **self._source_diagnostics(
                        source_scope,
                        index_build_id=None,
                        status=source_policy_status,
                    ),
                ),
            )

        identity, query_embedder = await self._resolve_query_embedder(active_build)
        self.resolved_query_embedder = query_embedder

        candidate_top_k = min(max(top_k * 2, top_k + 5), 100)
        if source_scope.effective_mode is SourcePolicyMode.ENFORCE:
            # Consolidation happens after ranking. Pull the full bounded
            # candidate window so lower-ranked distinct source groups can fill
            # the requested result count when higher ranks contain revisions
            # from the same logical source.
            candidate_top_k = 100
        multilingual_plan = None
        if strategy is RetrievalStrategy.HYBRID:
            multilingual_plan = await resolve_multilingual_plan(
                request.query,
                manifest=active_build.manifest,
                translation_config=self._query_translation_config,
                translator=self._query_translator,
                persist_translation_text=self._persist_translation_text,
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
            top_k=candidate_top_k,
            strategy=strategy,
            semantic_candidate_top_k=self._config.semantic_candidate_top_k,
            keyword_candidate_top_k=self._config.keyword_candidate_top_k,
            rrf_k=self._config.rrf_k,
            semantic_weight=self._config.semantic_weight,
            keyword_weight=self._config.keyword_weight,
            rerank_enabled=rerank_enabled,
            rerank_mode=self._config.rerank_mode if rerank_enabled else RerankMode.OFF,
            rerank_top_n=self._config.rerank_top_n,
            rerank_candidate_window=self._config.rerank_candidate_window,
            rerank_return_n=self._config.rerank_return_n,
            rerank_score_threshold=self._config.rerank_score_threshold,
            score_threshold=self._config.score_threshold,
            filterable_metadata_keys=tuple(self._config.filterable_metadata_keys),
            fts_regconfig=self._config.fts_regconfig,
            min_ocr_confidence=self._config.min_ocr_confidence,
            hnsw_ef_search=self._config.hnsw_ef_search,
            passage_scoring_enabled=self._config.passage_scoring_enabled,
            passage_window_tokens=self._config.passage_window_tokens,
            passage_overlap_tokens=self._config.passage_overlap_tokens,
            passage_min_tokens=self._config.passage_min_tokens,
            metadata={"request_strategy": strategy.value},
            source_scope=source_scope,
            multilingual_plan=multilingual_plan,
            persist_translation_text=self._persist_translation_text,
            modifies_expansion_enabled=self._config.modifies_expansion_enabled,
            max_related_sources=self._config.max_related_sources,
            max_relationship_candidates=self._config.max_relationship_candidates,
            source_metadata_reader=(
                self._source_metadata if source_scope.selectable is not None else None
            ),
        )

        retriever = self._build_retriever(strategy, query_embedder)
        reranked_candidates = await retriever.retrieve(context)
        reranked_candidate_count = len(reranked_candidates)
        policy = apply_source_policy(reranked_candidates, mode=source_scope.effective_mode)
        candidates = add_retrieval_provenance(
            policy.candidates,
            index_build_id=active_build.id,
            source_scope=source_scope,
            configuration_hash=self._configuration_hash,
            config_provenance=self._config_provenance,
        )
        hydrated_results = await self._hydrator.hydrate(candidates)
        candidate_trace = [
            _result_trace(result, rank=index)
            for index, result in enumerate(hydrated_results, start=1)
        ]
        suppression = self._duplicate_suppression.select(hydrated_results, limit=top_k)
        results = suppression.results
        post_rerank_removal_reasons = (
            {
                **policy.observed_exclusion_counts,
                **policy.consolidation_counts,
            }
            if source_scope.effective_mode is SourcePolicyMode.ENFORCE
            else {}
        )
        hydration_removed = len(policy.candidates) - len(hydrated_results)
        if hydration_removed:
            post_rerank_removal_reasons["hydration_missing"] = hydration_removed
        for reason, count in suppression.suppressed_by_reason.items():
            post_rerank_removal_reasons[reason] = (
                post_rerank_removal_reasons.get(reason, 0) + count
            )

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
            diversity_deferred_reasons=suppression.deferred_by_reason,
            diversity_backfilled_count=suppression.backfilled_count,
        )
        rerank_metadata = (
            results[0].metadata
            if results
            else (
                policy.candidates[0].metadata
                if policy.candidates
                else (reranked_candidates[0].metadata if reranked_candidates else {})
            )
        )
        plan_diagnostics = dict(multilingual_plan.diagnostics) if multilingual_plan else {}
        translation_meta = {**plan_diagnostics, **rerank_metadata}
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
        planned_branches = (
            [branch.branch_id for branch in multilingual_plan.branches]
            if multilingual_plan is not None
            else []
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
                reranker_score_scale=_optional_string(rerank_metadata.get("reranker_score_scale")),
                best_semantic_score=max(
                    (
                        result.semantic_score
                        for result in results
                        if result.semantic_score is not None
                    ),
                    default=None,
                ),
                best_passage_semantic_score=max(
                    (
                        result.passage_semantic_score
                        for result in results
                        if result.passage_semantic_score is not None
                    ),
                    default=None,
                ),
                passage_score_method=next(
                    (
                        result.passage_score_method
                        for result in results
                        if result.passage_score_method is not None
                    ),
                    None,
                ),
                duplicate_suppression_input_count=suppression.input_count,
                duplicate_suppression_removed_count=suppression.suppressed_count,
                duplicate_suppression_reasons=suppression.suppressed_by_reason,
                diversity_deferred_reasons=suppression.deferred_by_reason,
                diversity_backfilled_count=suppression.backfilled_count,
                candidate_trace=candidate_trace,
                selected_trace=[
                    _result_trace(result, rank=index)
                    for index, result in enumerate(results, start=1)
                ],
                compatibility_diagnostics=diagnostics,
                as_of=request.as_of,
                source_policy_consolidation_reasons=policy.consolidation_counts,
                query_language_profile=_optional_string(
                    translation_meta.get("query_language_profile")
                ),
                corpus_language_inventory=_int_dict(
                    multilingual_plan.inventory.chunk_language_counts if multilingual_plan else {}
                ),
                translation_status=_optional_string(translation_meta.get("translation_status")),
                translation_source_language=_optional_string(
                    translation_meta.get("translation_source_language")
                    or (
                        (
                            multilingual_plan.query_profile.exact_primary
                            or multilingual_plan.query_profile.profile
                        )
                        if multilingual_plan is not None
                        else None
                    )
                ),
                translation_provider=_optional_string(translation_meta.get("translation_provider")),
                translation_model=_optional_string(translation_meta.get("translation_model")),
                translation_prompt_version=_optional_string(
                    translation_meta.get("translation_prompt_version")
                ),
                translation_latency_ms=_optional_int(
                    translation_meta.get("translation_latency_ms")
                ),
                translation_usage=_any_dict(translation_meta.get("translation_usage")),
                translation_target_language=_optional_string(
                    translation_meta.get("translation_target_language")
                    or (multilingual_plan.target_language if multilingual_plan else None)
                ),
                translation_failure_reason=_optional_string(
                    translation_meta.get("translation_failure_reason")
                ),
                translated_query=(
                    multilingual_plan.translated_query if multilingual_plan is not None else None
                ),
                executed_branches=_string_list(translation_meta.get("executed_branches"))
                or planned_branches,
                skipped_branches=_string_list(translation_meta.get("skipped_branches"))
                or (
                    list(multilingual_plan.skipped_branches)
                    if multilingual_plan is not None
                    else []
                ),
                branch_candidate_counts=_int_dict(translation_meta.get("branch_candidate_counts")),
                query_variants=[
                    _query_variant_trace(
                        variant,
                        include_text=self._persist_translation_text
                        or variant.variant_id == "original",
                    )
                    for variant in (multilingual_plan.query_variants if multilingual_plan else ())
                ],
                language_routing_status=_optional_string(
                    translation_meta.get("language_routing_status")
                ),
                embedding_identity_status="matched",
                embedding_provider=identity.provider,
                embedding_model=identity.model,
                embedding_dimensions=identity.dimensions,
                embedding_set_version=identity.embedding_set_version,
                reranker_latency_ms=_optional_int(rerank_metadata.get("reranker_latency_ms")),
                reranker_usage=_any_dict(rerank_metadata.get("reranker_usage")),
                modifies_expansion_status=str(
                    rerank_metadata.get(
                        "modifies_expansion_status",
                        (
                            "disabled"
                            if not self._config.modifies_expansion_enabled
                            else "no_candidates"
                        ),
                    )
                ),
                modifies_expansion_depth=1,
                modifies_expansion_records=_dict_list(
                    rerank_metadata.get("modifies_expansion_records")
                ),
                modifies_expansion_exclusion_reasons=_int_dict(
                    rerank_metadata.get("modifies_expansion_exclusion_reasons")
                ),
                related_source_count=_optional_int(
                    rerank_metadata.get("related_source_count")
                )
                or 0,
                relationship_candidate_count=_optional_int(
                    rerank_metadata.get("relationship_candidate_count")
                )
                or 0,
                reranked_candidate_count=(
                    _optional_int(rerank_metadata.get("reranked_candidate_count"))
                    or reranked_candidate_count
                ),
                post_rerank_removed_count=sum(post_rerank_removal_reasons.values()),
                post_rerank_removal_reasons=post_rerank_removal_reasons,
                post_rerank_unfilled_slots=max(0, top_k - len(results)),
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
        if effective_mode is SourcePolicyMode.OFF and not self._config.modifies_expansion_enabled:
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

    async def _resolve_query_embedder(
        self, active_build: IndexBuild
    ) -> tuple[EmbeddingIdentity, BaseEmbeddingProvider]:
        identity = identity_from_manifest(active_build)
        if identity is None:
            rows = await self._embeddings.list_distinct_identities(active_build.id)
            identity = identity_from_vector_rows(active_build, rows)
        if identity is None:
            raise unlabeled_identity_error(index_build_id=getattr(active_build, "id", None))
        if self._query_embedder_factory is not None:
            try:
                embedder = self._query_embedder_factory(identity)
            except ProviderError as exc:
                raise ServiceUnavailableError(
                    "The active index build requires an embedding provider that is not "
                    "available. Keep the previous provider credentials until rollback is "
                    "no longer needed, or rebuild and activate a new index.",
                    code="embedding_provider_unavailable",
                    context={
                        "provider": identity.provider,
                        "model": identity.model,
                        "dimensions": identity.dimensions,
                        "embedding_set_version": identity.embedding_set_version,
                        "provider_error": str(exc),
                    },
                ) from exc
        else:
            embedder = self._embedder
        if not identity.matches(embedder):
            raise incompatible_identity_error(identity)
        return identity, embedder

    def _build_retriever(
        self,
        strategy: RetrievalStrategy,
        embedder: BaseEmbeddingProvider,
    ) -> BaseRetriever:
        if strategy is RetrievalStrategy.HYBRID:
            return HybridRetriever(
                self._session,
                self._project_id,
                embedder,
                self._reranker,
                fts_regconfig=self._config.fts_regconfig,
            )
        return SemanticRetriever(
            self._session,
            self._project_id,
            embedder,
        )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            output[str(key)] = int(item)
    return output


def _any_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _result_trace(result: RetrievalResult, *, rank: int) -> dict[str, Any]:
    """Return stable, sanitized retrieval facts without content or vectors."""
    branch_provenance = _branch_provenance(result.metadata)
    return {
        "rank": rank,
        "chunk_id": str(result.chunk_id),
        "document_id": str(result.document_id),
        "chunk_index": result.chunk_index,
        "retrieval_source": result.metadata.get("retrieval_source"),
        "score": result.score,
        "score_scale": result.metadata.get(
            "reranker_score_scale",
            "reciprocal_rank_fusion",
        ),
        "semantic_score": result.semantic_score,
        "rank_score": result.rank_score,
        "rerank_relevance_score": result.rerank_relevance_score,
        "passage_semantic_score": result.passage_semantic_score,
        "passage_score_method": result.passage_score_method,
        "passage_char_start": result.passage_char_start,
        "passage_char_end": result.passage_char_end,
        "rerank_status": result.metadata.get("rerank_status"),
        "rrf_rank": rank,
        "rrf_score": _fused_rrf_score(branch_provenance, result),
        "branch_provenance": branch_provenance,
        "original_dense": branch_provenance.get("original_dense"),
        "original_lexical": branch_provenance.get("original_lexical"),
        "translated_dense": _first_prefixed(branch_provenance, "translated_dense:"),
        "translated_lexical": _first_prefixed(branch_provenance, "translated_lexical:"),
    }


def _branch_provenance(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contributions = metadata.get("rrf_contributions")
    if not isinstance(contributions, list):
        return {}
    provenance: dict[str, dict[str, Any]] = {}
    for item in contributions:
        if not isinstance(item, dict) or item.get("branch_id") is None:
            continue
        branch_id = str(item["branch_id"])
        provenance[branch_id] = {
            "rank": item.get("rank"),
            "score": item.get("raw_score"),
            "rrf": item.get("rrf"),
            "family": item.get("family"),
            "query_variant_id": item.get("query_variant_id"),
            "target_language": item.get("target_language"),
            "score_type": item.get("score_type"),
        }
    return provenance


def _fused_rrf_score(
    provenance: dict[str, dict[str, Any]],
    result: RetrievalResult,
) -> float:
    contributions = [
        float(item["rrf"])
        for item in provenance.values()
        if isinstance(item.get("rrf"), (int, float)) and not isinstance(item.get("rrf"), bool)
    ]
    if contributions:
        return sum(contributions)
    if result.rank_score is not None:
        return result.rank_score
    return result.score


def _first_prefixed(
    provenance: dict[str, dict[str, Any]],
    prefix: str,
) -> dict[str, Any] | None:
    for branch_id, payload in provenance.items():
        if branch_id.startswith(prefix):
            return {"branch_id": branch_id, **payload}
    return None


def _query_variant_trace(variant: object, *, include_text: bool) -> dict[str, Any]:
    """Return provenance while respecting translated-query persistence policy."""
    kind = getattr(variant, "kind", None)
    return {
        "variant_id": str(getattr(variant, "variant_id", "")),
        "kind": getattr(kind, "value", str(kind) if kind is not None else None),
        "language": str(getattr(variant, "language", "und")),
        "text": str(getattr(variant, "text", "")) if include_text else None,
        "source_variant_id": getattr(variant, "source_variant_id", None),
        "translation_provider": getattr(variant, "translation_provider", None),
        "translation_model": getattr(variant, "translation_model", None),
        "translation_prompt_version": getattr(variant, "translation_prompt_version", None),
    }
