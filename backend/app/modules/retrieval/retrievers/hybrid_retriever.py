"""Hybrid retrieval orchestrator — RRF fusion and optional reranking."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RerankMode
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
            ranked_lists = [
                RankedList(
                    hits=semantic_batch.hits,
                    weight=context.semantic_weight,
                    branch_id=BRANCH_ORIGINAL_DENSE,
                    family=BRANCH_ORIGINAL_DENSE,
                ),
                RankedList(
                    hits=keyword_hits,
                    weight=context.keyword_weight,
                    branch_id=BRANCH_ORIGINAL_LEXICAL,
                    family=BRANCH_ORIGINAL_LEXICAL,
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

        final_candidates = _annotate_candidates(final_candidates, **multilingual_diagnostics)
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
                    metadata={
                        **source.metadata,
                        **result.metadata,
                        "rerank_status": "applied",
                        "reranker_provider": response.provider,
                        "reranker_model": response.model,
                        "reranker_version": response.provider_version,
                        "reranker_score_scale": response.score_scale.value,
                        "reranker_usage": dict(response.usage),
                        "reranker_latency_ms": response.latency_ms,
                    },
                )
            )

        if not reranked:
            return _annotate_candidates(fused, rerank_status="empty")
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
