"""Search schemas and stable retrieval DTOs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import RetrievalStrategy
from app.platform.domain.evidence_contracts import BranchContribution, QueryVariant


class SearchRequest(BaseModel):
    """Search request body."""

    query: str = Field(min_length=1, max_length=32_000)
    top_k: int | None = Field(default=None, ge=1, le=100)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    strategy: RetrievalStrategy | None = None
    rerank: bool | None = Field(default=None, deprecated=True)
    as_of: datetime | None = None


class RetrievalResult(BaseModel):
    """Stable search hit DTO for API and future Chat integration."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float
    semantic_score: float | None = None
    rank_score: float | None = None
    rerank_relevance_score: float | None = None
    evidence_relevance_score: float | None = None
    evidence_score_method: str | None = None
    evidence_calibration_id: str | None = None
    passage_semantic_score: float | None = None
    passage_char_start: int | None = None
    passage_char_end: int | None = None
    passage_score_method: str | None = None
    filename: str
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    query_variants: tuple[QueryVariant, ...] = ()
    branch_contributions: tuple[BranchContribution, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchDiagnostics(BaseModel):
    """Sanitized execution facts used by quality evaluation and operators."""

    strategy: RetrievalStrategy
    duration_ms: int
    rerank_requested: bool
    rerank_status: str
    rerank_failure_reason: str | None = None
    reranker_provider: str | None = None
    reranker_model: str | None = None
    reranker_version: str | None = None
    reranker_score_scale: str | None = None
    best_semantic_score: float | None = None
    best_passage_semantic_score: float | None = None
    passage_score_method: str | None = None
    duplicate_suppression_input_count: int = 0
    duplicate_suppression_removed_count: int = 0
    duplicate_suppression_reasons: dict[str, int] = Field(default_factory=dict)
    diversity_deferred_reasons: dict[str, int] = Field(default_factory=dict)
    diversity_backfilled_count: int = 0
    candidate_trace: list[dict[str, Any]] = Field(default_factory=list)
    selected_trace: list[dict[str, Any]] = Field(default_factory=list)
    compatibility_diagnostics: list[str] = Field(default_factory=list)
    as_of: datetime | None = None
    reference_date: date | None = None
    index_build_id: uuid.UUID | None = None
    source_metadata_generation: int = 0
    source_policy_configured_mode: str = "off"
    source_policy_effective_mode: str = "off"
    source_policy_deployment_cap: str = "enforce"
    source_policy_status: str = "off"
    source_policy_exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    source_policy_consolidation_reasons: dict[str, int] = Field(default_factory=dict)
    configuration_hash: str | None = None
    config_provenance: dict[str, Any] = Field(default_factory=dict)
    query_language_profile: str | None = None
    corpus_language_inventory: dict[str, int] = Field(default_factory=dict)
    translation_status: str | None = None
    skipped_reason: str | None = None
    translation_source_language: str | None = None
    translation_provider: str | None = None
    translation_model: str | None = None
    translation_prompt_version: str | None = None
    translation_latency_ms: int | None = None
    translation_usage: dict[str, Any] = Field(default_factory=dict)
    translation_target_language: str | None = None
    translation_failure_reason: str | None = None
    translation_attempts: int | None = None
    translation_validation_reasons: list[str] = Field(default_factory=list)
    translation_finish_reason: str | None = None
    translated_query: str | None = None
    executed_branches: list[str] = Field(default_factory=list)
    skipped_branches: list[str] = Field(default_factory=list)
    branch_candidate_counts: dict[str, int] = Field(default_factory=dict)
    query_variants: list[dict[str, Any]] = Field(default_factory=list)
    language_routing_status: str | None = None
    romanized_or_codeswitched: bool = False
    embedding_identity_status: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_set_version: int | None = None
    reranker_latency_ms: int | None = None
    reranker_usage: dict[str, Any] = Field(default_factory=dict)
    modifies_expansion_status: str = "disabled"
    modifies_expansion_depth: int = 1
    modifies_expansion_records: list[dict[str, Any]] = Field(default_factory=list)
    modifies_expansion_exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    modifies_authority_scope_status: str = "not_applicable"
    modifies_authority_unscoped_count: int = 0
    related_source_count: int = 0
    relationship_candidate_count: int = 0
    reranked_candidate_count: int = 0
    retrieved_candidate_count: int = 0
    post_rerank_removed_count: int = 0
    post_rerank_removal_reasons: dict[str, int] = Field(default_factory=dict)
    post_rerank_unfilled_slots: int = 0


class SearchResponse(BaseModel):
    """Search response wrapper."""

    results: list[RetrievalResult]
    query: str
    top_k: int
    diagnostics: SearchDiagnostics = Field(
        default_factory=lambda: SearchDiagnostics(
            strategy=RetrievalStrategy.SEMANTIC,
            duration_ms=0,
            rerank_requested=False,
            rerank_status="not_recorded",
        )
    )
