"""Hybrid retrieval orchestrator — RRF fusion and optional reranking."""

from __future__ import annotations

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
        semantic_batch = await self._semantic.retrieve_batch(context)
        semantic_hits = semantic_batch.hits
        keyword_hits = await self._keyword.retrieve(context)

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
        missing_semantic_ids = [
            candidate.chunk_id for candidate in fused if candidate.semantic_score is None
        ]
        if missing_semantic_ids:
            backfilled = await self._semantic.score_chunk_ids(
                context,
                missing_semantic_ids,
                query_vector=semantic_batch.query_vector,
                provider=semantic_batch.provider,
                model=semantic_batch.model,
            )
            unresolved = [
                chunk_id for chunk_id in missing_semantic_ids if chunk_id not in backfilled
            ]
            if unresolved:
                logger.warning(
                    "semantic_score_backfill_missing_vectors",
                    project_id=str(context.project_id),
                    index_build_id=str(context.index_build_id),
                    missing_chunk_count=len(unresolved),
                    missing_chunk_ids=[str(chunk_id) for chunk_id in unresolved],
                )
            fused = [
                CandidateHit(
                    chunk_id=candidate.chunk_id,
                    score=candidate.score,
                    source=candidate.source,
                    semantic_score=(
                        candidate.semantic_score
                        if candidate.semantic_score is not None
                        else backfilled.get(candidate.chunk_id)
                    ),
                    metadata=dict(candidate.metadata),
                )
                for candidate in fused
            ]

        if context.passage_scoring_enabled and fused:
            fused = await self._score_passage_evidence(
                context,
                fused,
                query_vector=semantic_batch.query_vector,
            )

        final_candidates = fused
        if context.rerank_enabled and fused:
            if self._reranker.is_passthrough:
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
            embedded = await self._embedder.embed_texts(passages)
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
                CandidateHit(
                    chunk_id=candidate.chunk_id,
                    score=candidate.score,
                    source=candidate.source,
                    semantic_score=candidate.semantic_score,
                    metadata=metadata,
                )
            )
        return output

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
                    semantic_score=(
                        source_by_id[result.chunk_id].semantic_score
                        if result.chunk_id in source_by_id
                        else None
                    ),
                    metadata={
                        **result.metadata,
                        "rerank_status": "applied",
                        "reranker_provider": response.provider,
                        "reranker_model": response.model,
                        "reranker_version": response.provider_version,
                        "reranker_score_scale": response.score_scale.value,
                    },
                )
            )

        if not reranked:
            return _annotate_candidates(fused, rerank_status="empty")
        return reranked


def _annotate_candidates(
    candidates: list[CandidateHit],
    **metadata: object,
) -> list[CandidateHit]:
    return [
        CandidateHit(
            chunk_id=candidate.chunk_id,
            score=candidate.score,
            source=candidate.source,
            semantic_score=candidate.semantic_score,
            metadata={**candidate.metadata, **metadata},
        )
        for candidate in candidates
    ]
