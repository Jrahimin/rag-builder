"""Unit tests for SearchServiceRetrievalAdapter."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.dependencies.conversations import SearchServiceRetrievalAdapter
from app.modules.retrieval.schemas.search import RetrievalResult, SearchResponse
from app.platform.domain.evidence_contracts import (
    BranchContribution,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)

pytestmark = pytest.mark.unit


async def test_adapter_maps_search_results_to_context_chunks() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    search_service = AsyncMock()
    variant = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text="refund",
    )
    contribution = BranchContribution(
        branch_id="original_lexical",
        family="original_lexical",
        query_variant_id="original",
        target_language=None,
        rank=1,
        raw_score=4.0,
        score_type=BranchScoreType.KEYWORD_BM25,
        rrf_score=0.01,
    )
    search_service.search = AsyncMock(
        return_value=SearchResponse(
            query="refund",
            top_k=5,
            results=[
                RetrievalResult(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    chunk_index=0,
                    content="refund within 30 days",
                    score=0.91,
                    semantic_score=0.77,
                    filename="policy.txt",
                    query_variants=(variant,),
                    branch_contributions=(contribution,),
                )
            ],
        )
    )
    adapter = SearchServiceRetrievalAdapter(search_service)
    result = await adapter.retrieve(query="refund", top_k=5)
    assert len(result.chunks) == 1
    assert result.chunks[0].chunk_id == chunk_id
    assert result.chunks[0].filename == "policy.txt"
    assert result.chunks[0].semantic_score == 0.77
    assert len(result.chunks[0].chunk_hash) == 64
    assert result.chunks[0].query_variants == (variant,)
    assert result.chunks[0].branch_contributions == (contribution,)
    assert result.diagnostics["source_policy_status"] == "off"
