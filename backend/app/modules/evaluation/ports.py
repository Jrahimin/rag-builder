"""Cross-module ports consumed by the evaluation runner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class QualityHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    filename: str
    chunk_index: int
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


@dataclass(frozen=True, slots=True)
class QualityAnswer:
    answer: str
    insufficient_evidence_reason: str | None
    grounded: bool
    citation_coverage: float
    claims: list[dict[str, Any]]


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
    ) -> QualitySearchResult: ...


class EvaluationAnswerPort(Protocol):
    async def answer(self, *, question: str, hits: list[QualityHit]) -> QualityAnswer: ...
