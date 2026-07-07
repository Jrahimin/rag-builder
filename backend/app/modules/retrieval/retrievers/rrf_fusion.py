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


def reciprocal_rank_fusion(
  ranked_lists: list[RankedList],
  *,
  rrf_k: int,
  top_k: int,
) -> list[CandidateHit]:
    """Fuse multiple ranked lists using weighted reciprocal rank fusion."""
    fused_scores: dict[uuid.UUID, float] = {}
    best_ranks: dict[uuid.UUID, int] = {}
    metadata_by_chunk: dict[uuid.UUID, dict] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked.hits, start=1):
            fused_scores[hit.chunk_id] = fused_scores.get(hit.chunk_id, 0.0) + (
                ranked.weight / (rrf_k + rank)
            )
            best_ranks[hit.chunk_id] = min(best_ranks.get(hit.chunk_id, rank), rank)
            metadata_by_chunk.setdefault(hit.chunk_id, {}).update(hit.metadata)

    ordered_ids = sorted(
        fused_scores.keys(),
        key=lambda chunk_id: (
            -fused_scores[chunk_id],
            best_ranks[chunk_id],
            str(chunk_id),
        ),
    )[:top_k]

    return [
        CandidateHit(
            chunk_id=chunk_id,
            score=fused_scores[chunk_id],
            source=CandidateSource.HYBRID,
            metadata=dict(metadata_by_chunk.get(chunk_id, {})),
        )
        for chunk_id in ordered_ids
    ]
