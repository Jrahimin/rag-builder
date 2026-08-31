"""Failure behavior for hybrid reranking."""

from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import RerankMode, RetrievalStrategy
from app.modules.retrieval.language_scope import LanguageScope
from app.modules.retrieval.multilingual.planner import (
    BRANCH_ORIGINAL_DENSE,
    BRANCH_ORIGINAL_LEXICAL,
    BRANCH_TRANSLATED_DENSE,
    BRANCH_TRANSLATED_LEXICAL,
    LanguageInventory,
    MultilingualRetrievalPlan,
    RetrievalBranch,
)
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever, _rerank_skip_reason
from app.modules.retrieval.retrievers.models import (
    CandidateHit,
    CandidateSource,
    RetrievalContext,
    RetrievalFilters,
)
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetrievalBatch
from app.platform.domain.language_detection import detect_query_language_profile
from app.platform.providers.contracts.embedding import EmbeddingBatchResult
from app.platform.providers.contracts.reranker import RerankResponse, RerankResult, RerankScoreScale
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider

pytestmark = pytest.mark.unit


def _context(*, rerank_enabled: bool = False) -> RetrievalContext:
    return RetrievalContext(
        project_id=uuid.uuid4(),
        query="refund policy",
        embedding_set_version=2,
        filters=RetrievalFilters(),
        top_k=5,
        strategy=RetrievalStrategy.HYBRID,
        semantic_candidate_top_k=10,
        keyword_candidate_top_k=10,
        rrf_k=60,
        semantic_weight=1.0,
        keyword_weight=1.0,
        rerank_enabled=rerank_enabled,
        rerank_top_n=5,
        rerank_score_threshold=None,
        score_threshold=None,
        filterable_metadata_keys=(),
        index_build_id=uuid.uuid4(),
    )


async def test_keyword_only_candidate_gets_batch_semantic_score_backfill() -> None:
    semantic_id = uuid.uuid4()
    keyword_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.return_value = SemanticRetrievalBatch(
        hits=[
            CandidateHit(
                semantic_id,
                0.8,
                CandidateSource.SEMANTIC,
                semantic_score=0.8,
            )
        ],
        query_vector=[0.1, 0.2],
        provider="provider",
        model="model",
    )
    retriever._semantic.score_chunk_ids.return_value = {keyword_id: 0.65}
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.return_value = [
        CandidateHit(keyword_id, 9.0, CandidateSource.KEYWORD)
    ]

    results = await retriever.retrieve(_context())

    by_id = {result.chunk_id: result for result in results}
    assert by_id[semantic_id].semantic_score == 0.8
    assert by_id[keyword_id].semantic_score == 0.65
    retriever._semantic.score_chunk_ids.assert_awaited_once()


async def test_keyword_only_candidate_without_a_vector_is_logged_as_missing_evidence() -> None:
    keyword_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.return_value = SemanticRetrievalBatch(
        hits=[],
        query_vector=[0.1, 0.2],
        provider="provider",
        model="model",
    )
    retriever._semantic.score_chunk_ids.return_value = {}
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.return_value = [
        CandidateHit(keyword_id, 9.0, CandidateSource.KEYWORD)
    ]

    with patch("app.modules.retrieval.retrievers.hybrid_retriever.logger") as logger:
        results = await retriever.retrieve(_context())

    assert results[0].semantic_score is None
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "semantic_score_backfill_missing_vectors"


async def test_passthrough_reranker_skips_content_load_and_keeps_fused_source() -> None:
    chunk_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.return_value = SemanticRetrievalBatch(
        hits=[CandidateHit(chunk_id, 0.8, CandidateSource.SEMANTIC, semantic_score=0.8)],
        query_vector=[0.1, 0.2],
        provider="provider",
        model="model",
    )
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.return_value = []
    retriever._content_loader = AsyncMock()
    retriever._reranker = NoopRerankerProvider()

    results = await retriever.retrieve(_context(rerank_enabled=True))

    retriever._content_loader.load_texts.assert_not_called()
    assert results[0].source is CandidateSource.HYBRID
    assert results[0].metadata["rerank_status"] == "passthrough"
    assert results[0].metadata["reranker_score_scale"] == "reciprocal_rank_fusion"


async def test_reranker_unavailable_preserves_fused_order_and_marks_fallback() -> None:
    chunk_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._content_loader = AsyncMock()
    retriever._content_loader.load_texts.return_value = {chunk_id: "refund policy"}
    retriever._reranker = AsyncMock()
    retriever._reranker.rerank.side_effect = ProviderError(
        "offline",
        provider_name="learned-reranker",
    )
    context = _context(rerank_enabled=True)
    fused = [CandidateHit(chunk_id, 0.03, CandidateSource.HYBRID)]

    result = await retriever._rerank_candidates(context, fused)

    assert [item.chunk_id for item in result] == [chunk_id]
    assert result[0].metadata["rerank_status"] == "unavailable"
    assert result[0].metadata["reranker_provider"] == "learned-reranker"


async def test_applied_rerank_copies_relevance_onto_evidence_fields() -> None:
    chunk_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._content_loader = AsyncMock()
    retriever._content_loader.load_texts.return_value = {chunk_id: "উৎসে কর সংগ্রহের খাত"}
    retriever._reranker = AsyncMock()
    retriever._reranker.rerank.return_value = RerankResponse(
        results=[RerankResult(chunk_id=chunk_id, score=0.81)],
        provider="cohere",
        model="rerank-v4.0-pro",
        provider_version="1",
        score_scale=RerankScoreScale.MODEL_RELEVANCE,
        latency_ms=12,
    )
    fused = [
        CandidateHit(chunk_id, 0.03, CandidateSource.HYBRID, semantic_score=0.22),
    ]

    result = await retriever._rerank_candidates(_context(rerank_enabled=True), fused)

    assert result[0].rerank_relevance_score == pytest.approx(0.81)
    assert result[0].evidence_relevance_score == pytest.approx(0.81)
    assert result[0].evidence_score_method == "reranker_relevance"
    assert result[0].metadata["rerank_status"] == "applied"
    retriever._reranker.rerank.assert_awaited_once()


async def test_passage_scoring_keeps_raw_cosine_and_winning_offsets() -> None:
    chunk_id = uuid.uuid4()
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._content_loader = AsyncMock()
    retriever._content_loader.load_texts.return_value = {
        chunk_id: " ".join(f"token-{index}" for index in range(40))
    }
    retriever._embedder = AsyncMock()
    retriever._embedder.embed_texts.return_value = EmbeddingBatchResult(
        vectors=[[0.8, 0.6], [0.2, 0.98]],
        provider="test",
        model="test",
        dimensions=2,
        provider_version="1",
    )
    context = _context()
    context = replace(
        context,
        passage_window_tokens=24,
        passage_overlap_tokens=8,
        passage_min_tokens=8,
    )

    results = await retriever._score_passage_evidence(
        context,
        [CandidateHit(chunk_id, 0.03, CandidateSource.HYBRID, semantic_score=0.2)],
        query_vector=[1.0, 0.0],
    )

    assert results[0].metadata["passage_semantic_score"] == pytest.approx(0.8)
    assert results[0].metadata["passage_char_start"] == 0
    assert results[0].metadata["passage_char_end"] > 0
    assert results[0].semantic_score == 0.2


async def test_translated_plan_runs_target_language_dense_and_lexical() -> None:
    original_id = uuid.uuid4()
    translated_id = uuid.uuid4()
    translated_query = "উৎসে কর সংগ্রহের খাত"
    scope = LanguageScope.translated_target("bn")
    profile = detect_query_language_profile("what are the source tax deduction areas?")
    plan = MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=LanguageInventory(
            schema_version="2026-08-18.v1",
            chunk_language_counts={"bn": 8},
            document_language_counts={"bn": 1},
            is_legacy=False,
        ),
        translation_status="applied",
        target_language="bn",
        translated_query=translated_query,
        branches=(
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_DENSE,
                family=BRANCH_ORIGINAL_DENSE,
                query="what are the source tax deduction areas?",
                language_scope=None,
            ),
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_LEXICAL,
                family=BRANCH_ORIGINAL_LEXICAL,
                query="what are the source tax deduction areas?",
                language_scope=None,
            ),
            RetrievalBranch(
                branch_id=f"{BRANCH_TRANSLATED_DENSE}:bn",
                family=BRANCH_TRANSLATED_DENSE,
                query=translated_query,
                language_scope=scope,
                target_language="bn",
                record_semantic_score=False,
            ),
            RetrievalBranch(
                branch_id=f"{BRANCH_TRANSLATED_LEXICAL}:bn",
                family=BRANCH_TRANSLATED_LEXICAL,
                query=translated_query,
                language_scope=scope,
                target_language="bn",
            ),
        ),
        skipped_branches=(),
        diagnostics={"translation_status": "applied"},
    )
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.side_effect = [
        SemanticRetrievalBatch(
            hits=[
                CandidateHit(
                    original_id,
                    0.21,
                    CandidateSource.SEMANTIC,
                    semantic_score=0.21,
                )
            ],
            query_vector=[0.1, 0.2],
            provider="provider",
            model="model",
        ),
        SemanticRetrievalBatch(
            hits=[
                CandidateHit(
                    translated_id,
                    0.71,
                    CandidateSource.SEMANTIC,
                    metadata={"translated_dense_score": 0.71},
                )
            ],
            query_vector=[0.3, 0.4],
            provider="provider",
            model="model",
        ),
    ]
    retriever._semantic.score_chunk_ids.return_value = {translated_id: 0.18}
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.side_effect = [
        [],
        [CandidateHit(translated_id, 12.4, CandidateSource.KEYWORD)],
    ]
    retriever._reranker = NoopRerankerProvider()

    results = await retriever.retrieve(
        replace(
            _context(),
            multilingual_plan=plan,
            query="what are the source tax deduction areas?",
        )
    )

    dense_calls = retriever._semantic.retrieve_batch.await_args_list
    lexical_calls = retriever._keyword.retrieve.await_args_list
    assert dense_calls[0].kwargs["query"] == "what are the source tax deduction areas?"
    assert dense_calls[1].kwargs["query"] == translated_query
    assert dense_calls[1].kwargs["language_scope"] is scope
    assert lexical_calls[0].kwargs["query"] == "what are the source tax deduction areas?"
    assert lexical_calls[1].kwargs["query"] == translated_query
    assert lexical_calls[1].kwargs["language_scope"] is scope
    by_id = {result.chunk_id: result for result in results}
    translated = by_id[translated_id]
    contributions = {item["branch_id"]: item for item in translated.metadata["rrf_contributions"]}
    assert f"{BRANCH_TRANSLATED_DENSE}:bn" in contributions
    assert f"{BRANCH_TRANSLATED_LEXICAL}:bn" in contributions
    assert "translated_query" not in translated.metadata
    assert translated.metadata["translation_status"] == "applied"


def test_cross_language_rerank_skips_when_inventory_matches_query() -> None:
    context = replace(_context(rerank_enabled=True), rerank_mode=RerankMode.CROSS_LANGUAGE)
    assert _rerank_skip_reason(context) == "skipped_same_language"

    plan = MultilingualRetrievalPlan(
        query_profile=detect_query_language_profile("source tax"),
        inventory=LanguageInventory(
            schema_version="2026-08-18.v1",
            chunk_language_counts={"bn": 8},
            document_language_counts={"bn": 1},
            is_legacy=False,
        ),
        translation_status="disabled",
        target_language=None,
        translated_query=None,
        branches=(),
        skipped_branches=(),
        diagnostics={},
        cross_language_target="bn",
    )
    assert (
        _rerank_skip_reason(
            replace(
                _context(rerank_enabled=True),
                rerank_mode=RerankMode.CROSS_LANGUAGE,
                multilingual_plan=plan,
            )
        )
        is None
    )
    assert (
        _rerank_skip_reason(replace(_context(rerank_enabled=True), rerank_mode=RerankMode.OFF))
        == "disabled"
    )
