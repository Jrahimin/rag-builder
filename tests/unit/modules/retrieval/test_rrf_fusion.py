"""Unit tests for RRF fusion."""

from __future__ import annotations

import uuid

import pytest

from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.retrievers.rrf_fusion import RankedList, reciprocal_rank_fusion

pytestmark = pytest.mark.unit


def test_rrf_prefers_chunks_ranked_high_in_both_lists() -> None:
    shared = uuid.uuid4()
    semantic_only = uuid.uuid4()
    keyword_only = uuid.uuid4()
    fused = reciprocal_rank_fusion(
        [
            RankedList(
                hits=[
                    CandidateHit(shared, 0.9, CandidateSource.SEMANTIC),
                    CandidateHit(semantic_only, 0.8, CandidateSource.SEMANTIC),
                ],
                weight=1.0,
            ),
            RankedList(
                hits=[
                    CandidateHit(shared, 8.0, CandidateSource.KEYWORD),
                    CandidateHit(keyword_only, 7.0, CandidateSource.KEYWORD),
                ],
                weight=1.0,
            ),
        ],
        rrf_k=60,
        top_k=3,
    )
    assert fused[0].chunk_id == shared
    assert fused[0].source is CandidateSource.HYBRID


def test_rrf_tie_breaks_by_best_source_rank_then_chunk_id() -> None:
    low_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    high_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    fused = reciprocal_rank_fusion(
        [
            RankedList(
                hits=[
                    CandidateHit(low_id, 1.0, CandidateSource.SEMANTIC),
                    CandidateHit(high_id, 0.9, CandidateSource.SEMANTIC),
                ],
                weight=1.0,
            ),
            RankedList(
                hits=[
                    CandidateHit(high_id, 1.0, CandidateSource.KEYWORD),
                    CandidateHit(low_id, 0.9, CandidateSource.KEYWORD),
                ],
                weight=1.0,
            ),
        ],
        rrf_k=60,
        top_k=2,
    )
    assert len(fused) == 2
    assert fused[0].score == fused[1].score
    assert fused[0].chunk_id == low_id
