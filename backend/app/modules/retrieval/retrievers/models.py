"""Neutral retrieval domain models shared by retrievers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import ModifiesExpansionMode, RerankMode, RetrievalStrategy
from app.platform.domain.evidence_contracts import BranchContribution, QueryVariant


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
    document_ids: tuple[uuid.UUID, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CandidateHit:
    """Lightweight ranked candidate — no chunk/document hydration."""

    chunk_id: uuid.UUID
    score: float
    source: CandidateSource
    metadata: dict[str, Any] = field(default_factory=dict)
    semantic_score: float | None = None
    rank_score: float | None = None
    rerank_relevance_score: float | None = None
    evidence_relevance_score: float | None = None
    evidence_score_method: str | None = None
    evidence_calibration_id: str | None = None
    query_variants: tuple[QueryVariant, ...] = ()
    branch_contributions: tuple[BranchContribution, ...] = ()


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
    index_build_id: uuid.UUID = field(default_factory=lambda: uuid.UUID(int=0))
    rerank_mode: RerankMode = RerankMode.ALWAYS
    fts_regconfig: str = "simple"
    min_ocr_confidence: float | None = None
    hnsw_ef_search: int = 100
    passage_scoring_enabled: bool = False
    passage_window_tokens: int = 96
    passage_overlap_tokens: int = 24
    passage_min_tokens: int = 32
    metadata: dict[str, Any] = field(default_factory=dict)
    source_scope: Any | None = None
    language_scope: Any | None = None
    rerank_candidate_window: int = 25
    rerank_return_n: int = 8
    multilingual_plan: Any | None = None
    persist_translation_text: bool = False
    modifies_expansion_enabled: bool = False
    modifies_expansion_mode: ModifiesExpansionMode = ModifiesExpansionMode.OFF
    max_related_sources: int = 8
    max_relationship_candidates: int = 20
    source_metadata_reader: Any | None = None

    def sanitized_metadata_filter(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.filters.metadata.items()
            if key in self.filterable_metadata_keys
        }
