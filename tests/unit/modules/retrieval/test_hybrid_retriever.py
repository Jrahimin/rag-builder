"""Failure behavior for hybrid reranking."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.config import RetrievalStrategy
from app.modules.retrieval.retrievers.hybrid_retriever import HybridRetriever
from app.modules.retrieval.retrievers.models import (
    CandidateHit,
    CandidateSource,
    RetrievalContext,
    RetrievalFilters,
)
from app.platform.providers.errors import ProviderError

pytestmark = pytest.mark.unit


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
    context = RetrievalContext(
        project_id=uuid.uuid4(),
        query="refund policy",
        embedding_set_version=1,
        filters=RetrievalFilters(),
        top_k=5,
        strategy=RetrievalStrategy.HYBRID,
        semantic_candidate_top_k=10,
        keyword_candidate_top_k=10,
        rrf_k=60,
        semantic_weight=1.0,
        keyword_weight=1.0,
        rerank_enabled=True,
        rerank_top_n=5,
        rerank_score_threshold=None,
        score_threshold=None,
        filterable_metadata_keys=(),
    )
    fused = [CandidateHit(chunk_id, 0.03, CandidateSource.HYBRID)]

    result = await retriever._rerank_candidates(context, fused)

    assert [item.chunk_id for item in result] == [chunk_id]
    assert result[0].metadata["rerank_status"] == "unavailable"
    assert result[0].metadata["reranker_provider"] == "learned-reranker"
