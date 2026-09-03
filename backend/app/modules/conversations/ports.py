"""Retrieval port and neutral context DTOs for chat."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from app.platform.domain.content_hash import content_hash
from app.platform.domain.evidence_contracts import BranchContribution, QueryVariant


class _RetrievalResultLike(Protocol):
    @property
    def chunk_id(self) -> uuid.UUID: ...

    @property
    def document_id(self) -> uuid.UUID: ...

    @property
    def chunk_index(self) -> int: ...

    @property
    def content(self) -> str: ...

    @property
    def score(self) -> float: ...

    @property
    def filename(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ContextChunk:
    """Already-ranked chunk from retrieval, ready for context budgeting."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float
    filename: str
    chunk_hash: str
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
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    query_variants: tuple[QueryVariant, ...] = ()
    branch_contributions: tuple[BranchContribution, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_retrieval_result(cls, result: _RetrievalResultLike) -> ContextChunk:
        """Map a ranked search hit onto chat context without dropping rerank scores."""
        metadata = dict(getattr(result, "metadata", None) or {})
        content = str(result.content)
        score = float(result.score)
        rerank_score = getattr(result, "rerank_relevance_score", None)
        rank_score = getattr(result, "rank_score", None)
        evidence_score = getattr(result, "evidence_relevance_score", None)
        evidence_method = getattr(result, "evidence_score_method", None)
        return cls(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            chunk_index=int(result.chunk_index),
            content=content,
            score=score,
            filename=str(result.filename),
            chunk_hash=content_hash(content),
            semantic_score=getattr(result, "semantic_score", None),
            rank_score=rank_score,
            rerank_relevance_score=rerank_score,
            evidence_relevance_score=evidence_score,
            evidence_score_method=evidence_method,
            evidence_calibration_id=getattr(result, "evidence_calibration_id", None),
            passage_semantic_score=getattr(result, "passage_semantic_score", None),
            passage_char_start=getattr(result, "passage_char_start", None),
            passage_char_end=getattr(result, "passage_char_end", None),
            passage_score_method=getattr(result, "passage_score_method", None),
            page_number=getattr(result, "page_number", None),
            char_start=getattr(result, "char_start", None),
            char_end=getattr(result, "char_end", None),
            query_variants=tuple(getattr(result, "query_variants", ()) or ()),
            branch_contributions=tuple(getattr(result, "branch_contributions", ()) or ()),
            metadata=metadata,
        ).restore_applied_rerank_scores()

    def restore_applied_rerank_scores(self) -> ContextChunk:
        """Recover applied rerank scores when retrieval copied only ``score``."""
        if self.metadata.get("rerank_status") != "applied":
            return self
        rerank_score = self.rerank_relevance_score
        rank_score = self.rank_score
        evidence_score = self.evidence_relevance_score
        evidence_method = self.evidence_score_method
        if rerank_score is None and self.score > 0.0:
            rerank_score = self.score
        if rank_score is None:
            rank_score = self.score
        if evidence_score is None:
            evidence_score = rerank_score
        if not evidence_method and rerank_score is not None:
            evidence_method = "reranker_relevance"
        if (
            rerank_score == self.rerank_relevance_score
            and rank_score == self.rank_score
            and evidence_score == self.evidence_relevance_score
            and evidence_method == self.evidence_score_method
        ):
            return self
        return replace(
            self,
            rank_score=rank_score,
            rerank_relevance_score=rerank_score,
            evidence_relevance_score=evidence_score,
            evidence_score_method=evidence_method,
        )


@dataclass(frozen=True, slots=True)
class EvidenceUnit(ContextChunk):
    """One immutable admitted span carried through the generation lifecycle."""

    evidence_unit_id: str = ""
    source_chunk_hash: str = ""
    evidence_span_hash: str = ""
    evidence_char_start: int = 0
    evidence_char_end: int = 0
    span_derivation: str = "complete_chunk"
    query_variant_id: str = "original"
    corroboration_method: str = ""


@dataclass(frozen=True, slots=True)
class CandidateEvidenceAssessment:
    """Exactly one terminal assessment for a candidate presented to grounding."""

    candidate_rank: int
    chunk_id: uuid.UUID
    reranker_score: float | None
    reranker_threshold: float
    reranker_calibration_id: str | None
    calibration_status: str
    query_variant_ids: tuple[str, ...]
    branch_contributions: tuple[BranchContribution, ...]
    span_derivation: str | None
    evidence_char_start: int | None
    evidence_char_end: int | None
    evidence_span_hash: str | None
    evidence_unit_id: str | None
    original_semantic_score: float | None
    semantic_span_aligned: bool
    original_lexical_coverage: float
    translated_lexical_coverage: dict[str, float]
    translated_dense_scores: dict[str, float]
    corroboration_method: str | None
    query_variant_provenance_missing: bool
    passed: bool
    terminal_reason: str


@dataclass(frozen=True, slots=True)
class ContextRetrievalResult:
    """Ranked context plus the captured retrieval execution provenance."""

    chunks: list[ContextChunk]
    diagnostics: dict[str, Any] = field(default_factory=dict)


class RetrievalPort(Protocol):
    """Project-scoped retrieval seam — ranking owned by the adapter implementation."""

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        as_of: datetime | None = None,
    ) -> ContextRetrievalResult: ...
