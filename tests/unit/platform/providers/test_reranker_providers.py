"""Unit tests for reranker providers."""

from __future__ import annotations

import uuid

import pytest

from app.platform.providers.contracts.reranker import (
    RerankCandidate,
    RerankRequest,
    RerankScoreScale,
)
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider

pytestmark = pytest.mark.unit


async def test_noop_reranker_preserves_order_and_scores() -> None:
    chunk_id = uuid.uuid4()
    provider = NoopRerankerProvider()
    response = await provider.rerank(
        RerankRequest(
            query="refund policy",
            candidates=[
                RerankCandidate(
                    chunk_id=chunk_id,
                    text="refund within 30 days",
                    source_score=0.42,
                )
            ],
            top_n=5,
            metadata={"strategy": "hybrid"},
        )
    )
    assert len(response.results) == 1
    assert response.results[0].chunk_id == chunk_id
    assert response.results[0].score == 0.42
    assert response.score_scale is RerankScoreScale.RECIPROCAL_RANK_FUSION
    assert provider.is_passthrough is True


async def test_lexical_reranker_prefers_overlap() -> None:
    provider = LexicalRerankerProvider()
    strong_id = uuid.uuid4()
    weak_id = uuid.uuid4()
    response = await provider.rerank(
        RerankRequest(
            query="refund policy handbook",
            candidates=[
                RerankCandidate(
                    chunk_id=weak_id,
                    text="unrelated content",
                    source_score=0.9,
                ),
                RerankCandidate(
                    chunk_id=strong_id,
                    text="employee refund policy handbook section",
                    source_score=0.2,
                ),
            ],
            top_n=2,
        )
    )
    assert response.results[0].chunk_id == strong_id
    assert response.results[0].score > response.results[1].score
    assert response.score_scale is RerankScoreScale.LEXICAL_HEURISTIC


async def test_lexical_reranker_query_coverage_is_not_diluted_by_chunk_length() -> None:
    provider = LexicalRerankerProvider()
    short_id = uuid.uuid4()
    long_id = uuid.uuid4()
    response = await provider.rerank(
        RerankRequest(
            query="refund policy",
            candidates=[
                RerankCandidate(
                    chunk_id=short_id,
                    text="refund policy",
                    source_score=0.1,
                ),
                RerankCandidate(
                    chunk_id=long_id,
                    text=f"refund policy {'additional context ' * 100}",
                    source_score=0.1,
                ),
            ],
            top_n=2,
        )
    )

    assert response.results[0].score == response.results[1].score


async def test_embedding_reranker_reports_non_learned_hash_fallback() -> None:
    provider = EmbeddingRerankerProvider(
        HashEmbeddingProvider(model="hash", dimensions=32, provider_version="1")
    )
    response = await provider.rerank(
        RerankRequest(
            query="refund policy",
            candidates=[
                RerankCandidate(
                    chunk_id=uuid.uuid4(),
                    text="refund policy applies for thirty days",
                    source_score=0.2,
                )
            ],
            top_n=1,
        )
    )
    assert response.provider == "embedding"
    assert response.results[0].metadata["learned"] is False
    assert response.score_scale is RerankScoreScale.COSINE_SIMILARITY_01
