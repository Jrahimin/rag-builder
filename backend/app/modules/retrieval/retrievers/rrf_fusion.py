"""Reciprocal Rank Fusion for hybrid candidate merging."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource


@dataclass(frozen=True, slots=True)
class RankedList:
    """Weighted ranked list input for RRF."""

    hits: list[CandidateHit]
    weight: float
    branch_id: str = "unspecified"
    family: str = "unspecified"
    target_language: str | None = None


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
    contributions: dict[uuid.UUID, list[dict[str, object]]] = {}

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
                {
                    "branch_id": ranked.branch_id,
                    "family": ranked.family,
                    "target_language": ranked.target_language,
                    "rank": rank,
                    "raw_score": hit.score,
                    "rrf": rrf_value,
                }
            )

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
        metadata["rrf_contributions"] = list(contributions.get(chunk_id, []))
        score = fused_scores[chunk_id]
        fused.append(
            CandidateHit(
                chunk_id=chunk_id,
                score=score,
                source=CandidateSource.HYBRID,
                semantic_score=semantic_scores.get(chunk_id),
                rank_score=score,
                metadata=metadata,
            )
        )
    return fused
