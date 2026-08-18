"""Cross-module ports consumed by the evaluation runner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class QualityHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    filename: str
    chunk_index: int
    semantic_score: float | None = None
    rerank_relevance_score: float | None = None
    passage_semantic_score: float | None = None
    passage_char_start: int | None = None
    passage_char_end: int | None = None
    passage_score_method: str | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualitySearchResult:
    hits: list[QualityHit]
    latency_ms: int
    rerank_status: str
    reranker_provider: str | None = None
    reranker_model: str | None = None
    reranker_version: str | None = None
    reranker_score_scale: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityAnswer:
    answer: str
    insufficient_evidence_reason: str | None
    grounded: bool
    citation_coverage: float
    claims: list[dict[str, Any]]
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_latency_ms: int | None = None


class EvaluationRetrievalPort(Protocol):
    @property
    def profiles(self) -> tuple[str, ...]: ...

    @property
    def primary_profile(self) -> str: ...

    @property
    def profile_metadata(self) -> dict[str, dict[str, Any]]: ...

    async def search(
        self,
        *,
        profile: str,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None,
        metadata_filter: dict[str, str],
        as_of: datetime | None,
    ) -> QualitySearchResult: ...


class EvaluationAnswerPort(Protocol):
    async def answer(
        self,
        *,
        profile: str,
        question: str,
        hits: list[QualityHit],
    ) -> QualityAnswer: ...
