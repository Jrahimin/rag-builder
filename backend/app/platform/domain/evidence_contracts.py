"""Provider-neutral provenance contracts for retrieval evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

RERANKER_RELEVANCE_CALIBRATION_ID = "reranker_relevance:v1"


class QueryVariantKind(StrEnum):
    """How a runtime query variant was produced."""

    ORIGINAL = "original"
    TRANSLATED = "translated"


class BranchScoreType(StrEnum):
    """Meaning of a retrieval branch's raw score."""

    COSINE_SIMILARITY = "cosine_similarity"
    KEYWORD_BM25 = "keyword_bm25"


@dataclass(frozen=True, slots=True)
class QueryVariant:
    """One immutable runtime query with stable translation provenance."""

    variant_id: str
    kind: QueryVariantKind
    language: str
    text: str
    source_variant_id: str | None = None
    translation_provider: str | None = None
    translation_model: str | None = None
    translation_prompt_version: str | None = None
    translation_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BranchContribution:
    """Candidate-local contribution made by one retrieval branch."""

    branch_id: str
    family: str
    query_variant_id: str
    target_language: str | None
    rank: int
    raw_score: float
    score_type: BranchScoreType
    rrf_score: float
