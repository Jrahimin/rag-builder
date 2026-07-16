"""Neutral retrieval domain models shared by retrievers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import RetrievalStrategy


class CandidateSource(StrEnum):
    """Origin of a retrieval candidate."""

    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    HYBRID = "hybrid"
    RERANK = "rerank"


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Project-scoped search filters."""

    document_id: uuid.UUID | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CandidateHit:
    """Lightweight ranked candidate — no chunk/document hydration."""

    chunk_id: uuid.UUID
    score: float
    source: CandidateSource
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    """Immutable retrieval envelope passed to every retriever."""

    project_id: uuid.UUID
    query: str
    embedding_set_version: int
    filters: RetrievalFilters
    top_k: int
    strategy: RetrievalStrategy
    semantic_candidate_top_k: int
    keyword_candidate_top_k: int
    rrf_k: int
    semantic_weight: float
    keyword_weight: float
    rerank_enabled: bool
    rerank_top_n: int
    rerank_score_threshold: float | None
    score_threshold: float | None
    filterable_metadata_keys: tuple[str, ...]
    fts_regconfig: str = "simple"
    min_ocr_confidence: float | None = None
    hnsw_ef_search: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def sanitized_metadata_filter(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.filters.metadata.items()
            if key in self.filterable_metadata_keys
        }
