"""Reciprocal Rank Fusion for hybrid candidate merging."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.platform.domain.evidence_contracts import (
    BranchContribution,
    BranchScoreType,
    QueryVariant,
)


@dataclass(frozen=True, slots=True)
class RankedList:
    """Weighted ranked list input for RRF."""

    hits: list[CandidateHit]
    weight: float
    branch_id: str = "unspecified"
    family: str = "unspecified"
    target_language: str | None = None
    query_variant_id: str = "original"
    score_type: BranchScoreType = BranchScoreType.COSINE_SIMILARITY
    query_variant: QueryVariant | None = None


def reciprocal_rank_fusion(
    ranked_lists: list[RankedList],
    *,
    rrf_k: int,
    top_k: int,
) -> list[CandidateHit]:
    """Fuse multiple ranked lists using weighted reciprocal rank fusion."""
    fused_scores: dict[uuid.UUID, float] = {}
    semantic_scores: dict[uuid.UUID, float] = {}
    best_ranks: dict[uuid.UUID, int] = {}
    metadata_by_chunk: dict[uuid.UUID, dict] = {}
    contributions: dict[uuid.UUID, list[BranchContribution]] = {}
    variants_by_chunk: dict[uuid.UUID, dict[str, QueryVariant]] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked.hits, start=1):
            rrf_value = ranked.weight / (rrf_k + rank)
            fused_scores[hit.chunk_id] = fused_scores.get(hit.chunk_id, 0.0) + rrf_value
            if hit.semantic_score is not None:
                semantic_scores[hit.chunk_id] = max(
                    semantic_scores.get(hit.chunk_id, hit.semantic_score),
                    hit.semantic_score,
                )
            best_ranks[hit.chunk_id] = min(best_ranks.get(hit.chunk_id, rank), rank)
            metadata_by_chunk.setdefault(hit.chunk_id, {}).update(hit.metadata)
            contributions.setdefault(hit.chunk_id, []).append(
                BranchContribution(
                    branch_id=ranked.branch_id,
                    family=ranked.family,
                    query_variant_id=ranked.query_variant_id,
                    target_language=ranked.target_language,
                    rank=rank,
                    raw_score=hit.score,
                    score_type=ranked.score_type,
                    rrf_score=rrf_value,
                )
            )
            for variant in hit.query_variants:
                variants_by_chunk.setdefault(hit.chunk_id, {})[variant.variant_id] = variant
            if ranked.query_variant is not None:
                variants_by_chunk.setdefault(hit.chunk_id, {})[
                    ranked.query_variant.variant_id
                ] = ranked.query_variant

    ordered_ids = sorted(
        fused_scores.keys(),
        key=lambda chunk_id: (
            -fused_scores[chunk_id],
            best_ranks[chunk_id],
            str(chunk_id),
        ),
    )[:top_k]

    fused: list[CandidateHit] = []
    for chunk_id in ordered_ids:
        metadata = dict(metadata_by_chunk.get(chunk_id, {}))
        typed_contributions = tuple(contributions.get(chunk_id, []))
        metadata["rrf_contributions"] = [
            {
                "branch_id": item.branch_id,
                "family": item.family,
                "query_variant_id": item.query_variant_id,
                "target_language": item.target_language,
                "rank": item.rank,
                "raw_score": item.raw_score,
                "score_type": item.score_type.value,
                "rrf": item.rrf_score,
            }
            for item in typed_contributions
        ]
        score = fused_scores[chunk_id]
        fused.append(
            CandidateHit(
                chunk_id=chunk_id,
                score=score,
                source=CandidateSource.HYBRID,
                semantic_score=semantic_scores.get(chunk_id),
                rank_score=score,
                metadata=metadata,
                query_variants=tuple(variants_by_chunk.get(chunk_id, {}).values()),
                branch_contributions=typed_contributions,
            )
        )
    return fused
