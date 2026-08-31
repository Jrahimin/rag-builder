"""Hybrid retrieval orchestrator — RRF fusion and optional reranking."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from datetime import date

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ModifiesExpansionMode, RerankMode
from app.modules.retrieval.multilingual.planner import (
    BRANCH_ORIGINAL_DENSE,
    BRANCH_ORIGINAL_LEXICAL,
    BRANCH_TRANSLATED_DENSE,
    BRANCH_TRANSLATED_LEXICAL,
    MultilingualRetrievalPlan,
    RetrievalBranch,
)
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.candidate_content_loader import CandidateContentLoader
from app.modules.retrieval.retrievers.keyword_retriever import KeywordRetriever
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource, RetrievalContext
from app.modules.retrieval.retrievers.rrf_fusion import RankedList, reciprocal_rank_fusion
from app.modules.retrieval.retrievers.semantic_retriever import (
    SemanticRetrievalBatch,
    SemanticRetriever,
)
from app.modules.retrieval.source_policy import (
    ModifierExpansionOutcome,
    ModifierExpansionRecord,
)
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankCandidate,
    RerankRequest,
    RerankScoreScale,
)
from app.platform.providers.embedding_similarity import (
    BoundedPassage,
    bounded_token_passages,
    cosine_similarity,
)
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


class HybridRetriever(BaseRetriever):
    """Run semantic + keyword retrieval, fuse with RRF, optionally rerank."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        reranker: BaseRerankerProvider,
        *,
        fts_regconfig: str = "simple",
    ) -> None:
        self._semantic = SemanticRetriever(session, project_id, embedder)
        self._keyword = KeywordRetriever(session, project_id, fts_regconfig=fts_regconfig)
        self._content_loader = CandidateContentLoader(session, project_id)
        self._reranker = reranker
        self._embedder = embedder

    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]:
        started = time.perf_counter()
        # Both repositories intentionally share one AsyncSession and transaction so
        # the active build/source generation snapshot cannot drift between branches.
        plan = context.multilingual_plan
        ranked_lists: list[RankedList]
        original_query_vector: list[float] | None
        original_provider: str | None
        original_model: str | None
        branch_counts: dict[str, int] = {}
        executed_branches: list[str] = []
        skipped_branches: list[str] = []
        multilingual_diagnostics: dict[str, object] = {}

        if isinstance(plan, MultilingualRetrievalPlan):
            (
                ranked_lists,
                original_batch,
                branch_counts,
                executed_branches,
                skipped_branches,
            ) = await self._retrieve_planned_branches(context, plan)
            original_query_vector = original_batch.query_vector if original_batch else None
            original_provider = original_batch.provider if original_batch else None
            original_model = original_batch.model if original_batch else None
            multilingual_diagnostics = dict(plan.diagnostics)
            multilingual_diagnostics.update(
                {
                    "executed_branches": executed_branches,
                    "skipped_branches": skipped_branches,
                    "branch_candidate_counts": branch_counts,
                    "translation_status": plan.translation_status,
                    "translation_source_language": (
                        plan.query_profile.exact_primary or plan.query_profile.profile
                    ),
                    "translation_target_language": plan.target_language,
                    "query_language_profile": plan.query_profile.profile,
                }
            )
        else:
            semantic_batch = await self._semantic.retrieve_batch(context)
            keyword_hits = await self._keyword.retrieve(context)
            original_query_vector = semantic_batch.query_vector
            original_provider = semantic_batch.provider
            original_model = semantic_batch.model
            original_variant = QueryVariant(
                variant_id="original",
                kind=QueryVariantKind.ORIGINAL,
                language="und",
                text=context.query,
            )
            ranked_lists = [
                RankedList(
                    hits=semantic_batch.hits,
                    weight=context.semantic_weight,
                    branch_id=BRANCH_ORIGINAL_DENSE,
                    family=BRANCH_ORIGINAL_DENSE,
                    query_variant=original_variant,
                ),
                RankedList(
                    hits=keyword_hits,
                    weight=context.keyword_weight,
                    branch_id=BRANCH_ORIGINAL_LEXICAL,
                    family=BRANCH_ORIGINAL_LEXICAL,
                    score_type=BranchScoreType.KEYWORD_BM25,
                    query_variant=original_variant,
                ),
            ]
            branch_counts = {
                BRANCH_ORIGINAL_DENSE: len(semantic_batch.hits),
                BRANCH_ORIGINAL_LEXICAL: len(keyword_hits),
            }
            executed_branches = [BRANCH_ORIGINAL_DENSE, BRANCH_ORIGINAL_LEXICAL]
            multilingual_diagnostics = {
                "executed_branches": executed_branches,
                "branch_candidate_counts": branch_counts,
            }

        fusion_top_k = (
            max(context.rerank_candidate_window, context.rerank_top_n, context.top_k)
            if context.rerank_enabled
            else context.top_k
        )
        expansion_diagnostics: dict[str, object] = {
            "modifies_expansion_status": "disabled",
            "modifies_expansion_depth": 1,
            "modifies_expansion_records": [],
            "modifies_expansion_exclusion_reasons": {},
            "related_source_count": 0,
            "relationship_candidate_count": 0,
        }
        expansion_mode = _expansion_mode(context)
        if expansion_mode is not ModifiesExpansionMode.OFF and ranked_lists:
            base_fused = reciprocal_rank_fusion(
                ranked_lists,
                rrf_k=context.rrf_k,
                top_k=fusion_top_k,
            )
            retrieve_related = (
                expansion_mode is ModifiesExpansionMode.EXPAND
                and context.filters.document_id is None
            )
            (
                related_lists,
                related_counts,
                expansion_diagnostics,
            ) = await self._retrieve_modifier_branches(
                context,
                base_fused,
                plan,
                retrieve_related=retrieve_related,
            )
            if not retrieve_related:
                scoped = context.filters.document_id is not None
                status = (
                    "suppressed_document_scope"
                    if expansion_mode is ModifiesExpansionMode.EXPAND and scoped
                    else "observe"
                )
                expansion_diagnostics = {
                    **expansion_diagnostics,
                    "modifies_expansion_status": status,
                }
            ranked_lists.extend(related_lists)
            branch_counts.update(related_counts)
            executed_branches.extend(related_counts)
        fused = reciprocal_rank_fusion(
            ranked_lists,
            rrf_k=context.rrf_k,
            top_k=fusion_top_k,
        )
        if original_query_vector is not None and original_provider and original_model and fused:
            fused = await self._backfill_original_semantic_scores(
                context,
                fused,
                query_vector=original_query_vector,
                provider=original_provider,
                model=original_model,
            )

        if context.passage_scoring_enabled and fused and original_query_vector is not None:
            fused = await self._score_passage_evidence(
                context,
                fused,
                query_vector=original_query_vector,
            )

        final_candidates = fused
        if context.rerank_enabled and fused:
            skip_reason = _rerank_skip_reason(context)
            if skip_reason is not None:
                final_candidates = _annotate_candidates(
                    fused,
                    rerank_status=skip_reason,
                    reranker_provider=self._reranker.provider_name,
                    reranker_model=self._reranker.model_name,
                    reranker_version=self._reranker.provider_version,
                    reranker_score_scale=RerankScoreScale.RECIPROCAL_RANK_FUSION.value,
                )
            elif self._reranker.is_passthrough:
                final_candidates = _annotate_candidates(
                    fused,
                    rerank_status="passthrough",
                    reranker_provider=self._reranker.provider_name,
                    reranker_model=self._reranker.model_name,
                    reranker_version=self._reranker.provider_version,
                    reranker_score_scale=RerankScoreScale.RECIPROCAL_RANK_FUSION.value,
                )
            else:
                final_candidates = await self._rerank_candidates(context, fused)

        final_candidates = _annotate_candidates(
            final_candidates,
            **multilingual_diagnostics,
            **expansion_diagnostics,
            retrieved_candidate_count=len(fused),
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "hybrid_retrieve_complete",
            project_id=str(context.project_id),
            duration_ms=elapsed_ms,
            semantic_candidates=branch_counts.get(BRANCH_ORIGINAL_DENSE, 0),
            keyword_candidates=branch_counts.get(BRANCH_ORIGINAL_LEXICAL, 0),
            translated_dense_candidates=sum(
                count
                for branch_id, count in branch_counts.items()
                if branch_id.startswith(f"{BRANCH_TRANSLATED_DENSE}:")
            ),
            translated_lexical_candidates=sum(
                count
                for branch_id, count in branch_counts.items()
                if branch_id.startswith(f"{BRANCH_TRANSLATED_LEXICAL}:")
            ),
            executed_branches=executed_branches,
            fused_candidates=len(fused),
            final_candidates=len(final_candidates),
        )
        return_n = context.top_k
        if (
            context.rerank_enabled
            and not self._reranker.is_passthrough
            and final_candidates
            and final_candidates[0].metadata.get("rerank_status") == "applied"
        ):
            return_n = min(return_n, max(context.rerank_return_n, 1))
        return final_candidates[:return_n]

    async def _retrieve_modifier_branches(
        self,
        context: RetrievalContext,
        base_fused: list[CandidateHit],
        plan: object,
        *,
        retrieve_related: bool = True,
    ) -> tuple[list[RankedList], dict[str, int], dict[str, object]]:
        reader = context.source_metadata_reader
        base_revision_ids = tuple(
            dict.fromkeys(
                revision_id
                for candidate in base_fused
                if (revision_id := _uuid_value(candidate.metadata.get("source_revision_id")))
                is not None
            )
        )
        if reader is None or not base_revision_ids:
            status = "unavailable" if reader is None else "no_retrieved_governed_bases"
            return [], {}, _expansion_diagnostics(status=status, records=[])
        try:
            records = await reader.incoming_modifiers(
                project_id=context.project_id,
                base_revision_ids=base_revision_ids,
                generation=getattr(context.source_scope, "generation", 0),
                as_of=getattr(context.source_scope, "explicit_as_of", None),
                index_build_id=context.index_build_id,
            )
        except Exception as exc:
            logger.warning(
                "modifies_expansion_read_failed",
                project_id=str(context.project_id),
                error_type=type(exc).__name__,
            )
            return [], {}, _expansion_diagnostics(status="unavailable", records=[])
        bounded = _bound_modifier_records(
            records,
            base_revision_ids=set(base_revision_ids),
            base_document_ids={
                document_id
                for candidate in base_fused
                if (document_id := _uuid_value(candidate.metadata.get("source_document_id")))
                is not None
            },
            max_related_sources=context.max_related_sources,
        )
        selected = [item for item in bounded if item.outcome is ModifierExpansionOutcome.EXPANDED]
        if not selected:
            status = "no_relationships" if not bounded else "no_eligible_modifiers"
            return [], {}, _expansion_diagnostics(status=status, records=bounded)
        if not retrieve_related:
            return [], {}, _expansion_diagnostics(status="observe", records=bounded)

        selected_documents = tuple(dict.fromkeys(item.modifier_document_id for item in selected))
        related_context = replace(
            context,
            filters=replace(
                context.filters,
                document_id=None,
                document_ids=selected_documents,
            ),
            modifies_expansion_enabled=False,
            modifies_expansion_mode=ModifiesExpansionMode.OFF,
        )
        record_by_document = {item.modifier_document_id: item for item in selected}
        related_lists: list[RankedList] = []
        branch_counts: dict[str, int] = {}
        if isinstance(plan, MultilingualRetrievalPlan):
            raw_lists, _, _, _, _ = await self._retrieve_planned_branches(related_context, plan)
            for ranked in raw_lists:
                branch_id = f"related_modifier:{ranked.branch_id}"
                hits = _annotate_related_hits(ranked.hits, record_by_document)
                related_lists.append(replace(ranked, hits=hits, branch_id=branch_id))
                branch_counts[branch_id] = len(hits)
        else:
            semantic_batch = await self._semantic.retrieve_batch(related_context)
            keyword_hits = await self._keyword.retrieve(related_context)
            original_variant = QueryVariant(
                variant_id="original",
                kind=QueryVariantKind.ORIGINAL,
                language="und",
                text=context.query,
            )
            related_lists = [
                RankedList(
                    hits=_annotate_related_hits(
                        semantic_batch.hits,
                        record_by_document,
                    ),
                    weight=context.semantic_weight,
                    branch_id=f"related_modifier:{BRANCH_ORIGINAL_DENSE}",
                    family=BRANCH_ORIGINAL_DENSE,
                    query_variant=original_variant,
                ),
                RankedList(
                    hits=_annotate_related_hits(keyword_hits, record_by_document),
                    weight=context.keyword_weight,
                    branch_id=f"related_modifier:{BRANCH_ORIGINAL_LEXICAL}",
                    family=BRANCH_ORIGINAL_LEXICAL,
                    score_type=BranchScoreType.KEYWORD_BM25,
                    query_variant=original_variant,
                ),
            ]
            branch_counts = {item.branch_id: len(item.hits) for item in related_lists}

        raw_ids_by_document: dict[uuid.UUID, set[uuid.UUID]] = {}
        for ranked in related_lists:
            for hit in ranked.hits:
                document_id = _uuid_value(hit.metadata.get("source_document_id"))
                if document_id is not None:
                    raw_ids_by_document.setdefault(document_id, set()).add(hit.chunk_id)
        related_fused = reciprocal_rank_fusion(
            related_lists,
            rrf_k=context.rrf_k,
            top_k=context.max_relationship_candidates,
        )
        retained_ids = {candidate.chunk_id for candidate in related_fused}
        retained_ids_by_document: dict[uuid.UUID, set[uuid.UUID]] = {}
        for candidate in related_fused:
            document_id = _uuid_value(candidate.metadata.get("source_document_id"))
            if document_id is not None:
                retained_ids_by_document.setdefault(document_id, set()).add(candidate.chunk_id)
        bounded = [
            replace(
                item,
                outcome=(
                    ModifierExpansionOutcome.CANDIDATE_CAP_EXCEEDED
                    if item.outcome is ModifierExpansionOutcome.EXPANDED
                    and raw_ids_by_document.get(item.modifier_document_id)
                    and not retained_ids_by_document.get(item.modifier_document_id)
                    else item.outcome
                ),
                candidate_count=len(raw_ids_by_document.get(item.modifier_document_id, set())),
                retained_candidate_count=len(
                    retained_ids_by_document.get(item.modifier_document_id, set())
                ),
            )
            for item in bounded
        ]
        related_lists = [
            replace(
                ranked,
                hits=[hit for hit in ranked.hits if hit.chunk_id in retained_ids],
            )
            for ranked in related_lists
        ]
        related_lists = [ranked for ranked in related_lists if ranked.hits]
        status = "expanded" if related_fused else "expanded_no_candidates"
        return (
            related_lists,
            branch_counts,
            _expansion_diagnostics(
                status=status,
                records=bounded,
                relationship_candidate_count=len(related_fused),
            ),
        )

    async def _retrieve_planned_branches(
        self,
        context: RetrievalContext,
        plan: MultilingualRetrievalPlan,
    ) -> tuple[
        list[RankedList],
        SemanticRetrievalBatch | None,
        dict[str, int],
        list[str],
        list[str],
    ]:
        ranked_lists: list[RankedList] = []
        branch_counts: dict[str, int] = {}
        executed: list[str] = []
        skipped = list(plan.skipped_branches)
        original_batch: SemanticRetrievalBatch | None = None
        variants = {variant.variant_id: variant for variant in plan.query_variants}
        for branch in plan.branches:
            try:
                hits, batch = await self._execute_branch(context, branch)
            except ProviderError:
                logger.warning(
                    "retrieval_branch_unavailable",
                    project_id=str(context.project_id),
                    branch_id=branch.branch_id,
                    family=branch.family,
                )
                skipped.append(branch.branch_id)
                continue
            executed.append(branch.branch_id)
            branch_counts[branch.branch_id] = len(hits)
            if branch.family == BRANCH_ORIGINAL_DENSE:
                original_batch = batch
            weight = (
                context.semantic_weight
                if branch.family in {BRANCH_ORIGINAL_DENSE, BRANCH_TRANSLATED_DENSE}
                else context.keyword_weight
            )
            ranked_lists.append(
                RankedList(
                    hits=hits,
                    weight=weight,
                    branch_id=branch.branch_id,
                    family=branch.family,
                    target_language=branch.target_language,
                    query_variant_id=branch.query_variant_id,
                    score_type=(
                        BranchScoreType.COSINE_SIMILARITY
                        if branch.family in {BRANCH_ORIGINAL_DENSE, BRANCH_TRANSLATED_DENSE}
                        else BranchScoreType.KEYWORD_BM25
                    ),
                    query_variant=variants.get(branch.query_variant_id),
                )
            )
        return ranked_lists, original_batch, branch_counts, executed, skipped

    async def _execute_branch(
        self,
        context: RetrievalContext,
        branch: RetrievalBranch,
    ) -> tuple[list[CandidateHit], SemanticRetrievalBatch | None]:
        if branch.family in {BRANCH_ORIGINAL_DENSE, BRANCH_TRANSLATED_DENSE}:
            batch = await self._semantic.retrieve_batch(
                context,
                query=branch.query,
                language_scope=branch.language_scope,
                record_semantic_score=branch.record_semantic_score,
            )
            return batch.hits, batch
        hits = await self._keyword.retrieve(
            context,
            query=branch.query,
            language_scope=branch.language_scope,
        )
        return hits, None

    async def _backfill_original_semantic_scores(
        self,
        context: RetrievalContext,
        fused: list[CandidateHit],
        *,
        query_vector: list[float],
        provider: str,
        model: str,
    ) -> list[CandidateHit]:
        missing_semantic_ids = [
            candidate.chunk_id for candidate in fused if candidate.semantic_score is None
        ]
        if not missing_semantic_ids:
            return fused
        backfilled = await self._semantic.score_chunk_ids(
            context,
            missing_semantic_ids,
            query_vector=query_vector,
            provider=provider,
            model=model,
        )
        unresolved = [chunk_id for chunk_id in missing_semantic_ids if chunk_id not in backfilled]
        if unresolved:
            logger.warning(
                "semantic_score_backfill_missing_vectors",
                project_id=str(context.project_id),
                index_build_id=str(context.index_build_id),
                missing_chunk_count=len(unresolved),
                missing_chunk_ids=[str(chunk_id) for chunk_id in unresolved],
            )
        return [
            replace(
                candidate,
                semantic_score=(
                    candidate.semantic_score
                    if candidate.semantic_score is not None
                    else backfilled.get(candidate.chunk_id)
                ),
            )
            for candidate in fused
        ]

    async def _score_passage_evidence(
        self,
        context: RetrievalContext,
        candidates: list[CandidateHit],
        *,
        query_vector: list[float],
    ) -> list[CandidateHit]:
        texts = await self._content_loader.load_texts(
            [candidate.chunk_id for candidate in candidates]
        )
        passages: list[str] = []
        owners: list[tuple[uuid.UUID, BoundedPassage]] = []
        for candidate in candidates:
            for passage in bounded_token_passages(
                texts.get(candidate.chunk_id, ""),
                window_tokens=context.passage_window_tokens,
                overlap_tokens=context.passage_overlap_tokens,
                minimum_tokens=context.passage_min_tokens,
            ):
                passages.append(passage.text)
                owners.append((candidate.chunk_id, passage))
        if not passages:
            return candidates
        try:
            embedded = await self._embedder.embed_texts(
                passages,
                purpose=EmbeddingPurpose.DOCUMENT,
            )
        except ProviderError:
            logger.warning(
                "passage_semantic_scoring_unavailable",
                project_id=str(context.project_id),
                candidate_count=len(candidates),
            )
            return _annotate_candidates(candidates, passage_score_status="unavailable")

        best: dict[uuid.UUID, tuple[float, BoundedPassage]] = {}
        for (chunk_id, passage), vector in zip(owners, embedded.vectors, strict=True):
            score = cosine_similarity(query_vector, vector)
            current = best.get(chunk_id)
            if current is None or score > current[0]:
                best[chunk_id] = (score, passage)
        output: list[CandidateHit] = []
        for candidate in candidates:
            winner = best.get(candidate.chunk_id)
            metadata = dict(candidate.metadata)
            if winner is not None:
                score, passage = winner
                metadata.update(
                    {
                        "passage_semantic_score": score,
                        "passage_char_start": passage.char_start,
                        "passage_char_end": passage.char_end,
                        "passage_score_method": "bounded_token_max_v1",
                        "passage_score_status": "applied",
                    }
                )
            output.append(
                replace(
                    candidate,
                    metadata=metadata,
                )
            )
        return output

    async def _rerank_candidates(
        self,
        context: RetrievalContext,
        fused: list[CandidateHit],
    ) -> list[CandidateHit]:
        rerank_window = fused[: max(context.rerank_candidate_window, context.rerank_top_n)]
        texts = await self._content_loader.load_texts(
            [candidate.chunk_id for candidate in rerank_window]
        )
        top_n = min(len(rerank_window), max(context.rerank_return_n, 1))
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
            top_n=top_n,
            metadata=dict(context.metadata),
        )
        if not request.candidates:
            return fused

        try:
            response = await self._reranker.rerank(request)
        except ProviderError as exc:
            logger.warning(
                "rerank_failed_using_fused_order",
                project_id=str(context.project_id),
                provider=exc.provider_name,
            )
            return _annotate_candidates(
                fused,
                rerank_status="unavailable",
                reranker_provider=exc.provider_name,
                reranked_candidate_count=len(request.candidates),
            )

        reranked: list[CandidateHit] = []
        source_by_id = {candidate.chunk_id: candidate for candidate in rerank_window}
        for result in response.results:
            source = source_by_id.get(result.chunk_id)
            if source is None:
                continue
            if (
                context.rerank_score_threshold is not None
                and result.score < context.rerank_score_threshold
            ):
                continue
            reranked.append(
                replace(
                    source,
                    score=result.score,
                    source=CandidateSource.RERANK,
                    rank_score=result.score,
                    rerank_relevance_score=result.score,
                    evidence_relevance_score=result.score,
                    evidence_score_method="reranker_relevance",
                    evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
                    metadata={
                        **source.metadata,
                        **result.metadata,
                        "rerank_status": "applied",
                        "evidence_score_method": "reranker_relevance",
                        "reranker_provider": response.provider,
                        "reranker_model": response.model,
                        "reranker_version": response.provider_version,
                        "reranker_score_scale": response.score_scale.value,
                        "reranker_usage": dict(response.usage),
                        "reranker_latency_ms": response.latency_ms,
                        "reranked_candidate_count": len(request.candidates),
                    },
                )
            )

        if not reranked:
            return _annotate_candidates(
                fused,
                rerank_status="empty",
                reranked_candidate_count=len(request.candidates),
            )
        return reranked


def _rerank_skip_reason(context: RetrievalContext) -> str | None:
    if context.rerank_mode is RerankMode.OFF:
        return "disabled"
    if context.rerank_mode is not RerankMode.CROSS_LANGUAGE:
        return None
    plan = context.multilingual_plan
    target = getattr(plan, "cross_language_target", None) if plan is not None else None
    if target:
        return None
    return "skipped_same_language"


def _annotate_candidates(
    candidates: list[CandidateHit],
    **metadata: object,
) -> list[CandidateHit]:
    return [
        replace(candidate, metadata={**candidate.metadata, **metadata}) for candidate in candidates
    ]


def _bound_modifier_records(
    records: list[ModifierExpansionRecord],
    *,
    base_revision_ids: set[uuid.UUID],
    base_document_ids: set[uuid.UUID],
    max_related_sources: int,
) -> list[ModifierExpansionRecord]:
    visited_revisions = set(base_revision_ids)
    visited_documents = set(base_document_ids)
    selected_documents: set[uuid.UUID] = set()
    output: list[ModifierExpansionRecord] = []
    for item in sorted(records, key=_modifier_sort_key):
        outcome = item.outcome
        if outcome is ModifierExpansionOutcome.EXPANDED:
            if item.modifier_document_id in selected_documents:
                outcome = ModifierExpansionOutcome.DUPLICATE
            elif (
                item.modifier_revision_id == item.base_revision_id
                or item.modifier_document_id == item.base_document_id
            ):
                outcome = ModifierExpansionOutcome.CYCLE
            elif (
                item.modifier_revision_id in base_revision_ids
                or item.modifier_document_id in base_document_ids
            ):
                outcome = ModifierExpansionOutcome.ALREADY_IN_RECALL
            elif len(selected_documents) >= max_related_sources:
                outcome = ModifierExpansionOutcome.SOURCE_CAP_EXCEEDED
            else:
                selected_documents.add(item.modifier_document_id)
                visited_revisions.add(item.modifier_revision_id)
                visited_documents.add(item.modifier_document_id)
        output.append(replace(item, outcome=outcome))
    return output


def _modifier_sort_key(item: ModifierExpansionRecord) -> tuple[int, int, int, str]:
    return (
        -_date_ordinal(item.modifier_effective_from),
        -_date_ordinal(item.modifier_published_date),
        -(item.modifier_revision_number or 0),
        str(item.relationship_id),
    )


def _date_ordinal(value: str | None) -> int:
    try:
        return date.fromisoformat(value or "").toordinal()
    except ValueError:
        return 0


def _annotate_related_hits(
    hits: list[CandidateHit],
    record_by_document: dict[uuid.UUID, ModifierExpansionRecord],
) -> list[CandidateHit]:
    output: list[CandidateHit] = []
    for hit in hits:
        document_id = _uuid_value(hit.metadata.get("source_document_id"))
        record = record_by_document.get(document_id) if document_id is not None else None
        if record is None:
            continue
        output.append(
            replace(
                hit,
                metadata={
                    **hit.metadata,
                    "retrieval_scope": "related_modifier",
                    "relationship_grounding_trust": False,
                    "relationship_recall_provenance": [record.recall_provenance()],
                },
            )
        )
    return output


def _uuid_value(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _expansion_mode(context: RetrievalContext) -> ModifiesExpansionMode:
    if context.modifies_expansion_mode is not ModifiesExpansionMode.OFF:
        return context.modifies_expansion_mode
    if context.modifies_expansion_enabled:
        return ModifiesExpansionMode.EXPAND
    return ModifiesExpansionMode.OFF


def _expansion_diagnostics(
    *,
    status: str,
    records: list[ModifierExpansionRecord],
    relationship_candidate_count: int = 0,
) -> dict[str, object]:
    exclusions: dict[str, int] = {}
    for item in records:
        if item.outcome is ModifierExpansionOutcome.EXPANDED:
            continue
        exclusions[item.outcome.value] = exclusions.get(item.outcome.value, 0) + 1
    authority_applicable = [
        item
        for item in records
        if item.outcome
        in {ModifierExpansionOutcome.EXPANDED, ModifierExpansionOutcome.ALREADY_IN_RECALL}
    ]
    unscoped = sum(not item.target_provisions for item in authority_applicable)
    return {
        "modifies_expansion_status": status,
        "modifies_expansion_depth": 1,
        "modifies_expansion_records": [item.diagnostic() for item in records],
        "modifies_expansion_exclusion_reasons": exclusions,
        "modifies_authority_scope_status": (
            "unscoped_relationships"
            if unscoped
            else "scoped"
            if authority_applicable
            else "not_applicable"
        ),
        "modifies_authority_unscoped_count": unscoped,
        "related_source_count": len(
            {
                item.modifier_document_id
                for item in records
                if item.outcome is ModifierExpansionOutcome.EXPANDED
            }
        ),
        "relationship_candidate_count": relationship_candidate_count,
    }
