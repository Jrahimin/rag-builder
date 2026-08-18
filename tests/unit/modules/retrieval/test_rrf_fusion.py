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
                    CandidateHit(
                        shared,
                        0.9,
                        CandidateSource.SEMANTIC,
                        semantic_score=0.9,
                    ),
                    CandidateHit(
                        semantic_only,
                        0.8,
                        CandidateSource.SEMANTIC,
                        semantic_score=0.8,
                    ),
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
    assert fused[0].semantic_score == 0.9
    assert next(item for item in fused if item.chunk_id == keyword_only).semantic_score is None


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


def test_rrf_records_per_branch_rank_score_and_contribution() -> None:
    chunk_id = uuid.uuid4()
    fused = reciprocal_rank_fusion(
        [
            RankedList(
                hits=[CandidateHit(chunk_id, 0.21, CandidateSource.SEMANTIC, semantic_score=0.21)],
                weight=1.0,
                branch_id="original_dense",
                family="original_dense",
            ),
            RankedList(
                hits=[CandidateHit(chunk_id, 0.71, CandidateSource.SEMANTIC)],
                weight=1.0,
                branch_id="translated_dense:bn",
                family="translated_dense",
                target_language="bn",
            ),
            RankedList(
                hits=[CandidateHit(chunk_id, 11.2, CandidateSource.KEYWORD)],
                weight=1.0,
                branch_id="translated_lexical:bn",
                family="translated_lexical",
                target_language="bn",
            ),
        ],
        rrf_k=60,
        top_k=1,
    )
    contributions = {item["branch_id"]: item for item in fused[0].metadata["rrf_contributions"]}
    assert contributions["original_dense"]["rank"] == 1
    assert contributions["original_dense"]["raw_score"] == 0.21
    assert contributions["translated_dense:bn"]["rank"] == 1
    assert contributions["translated_dense:bn"]["raw_score"] == 0.71
    assert contributions["translated_lexical:bn"]["rank"] == 1
    assert fused[0].score == pytest.approx(sum(item["rrf"] for item in contributions.values()))
