"""Reranker provider contract and neutral DTOs."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RerankScoreScale(StrEnum):
    """Declared meaning of a reranker's score; never an implicit confidence."""

    RECIPROCAL_RANK_FUSION = "reciprocal_rank_fusion"
    LEXICAL_HEURISTIC = "lexical_heuristic"
    COSINE_SIMILARITY_01 = "cosine_similarity_01"
    MODEL_RELEVANCE = "model_relevance"


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """One candidate document for reranking."""

    chunk_id: uuid.UUID
    text: str
    source_score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankRequest:
    """Future-proof reranker input for text and multimodal extensions."""

    query: str
    candidates: list[RerankCandidate]
    top_n: int
    metadata: dict[str, Any] = field(default_factory=dict)
    # Extension slots for future images/tables/multimodal payloads.
    attachments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankResult:
    """One reranked candidate with relevance score."""

    chunk_id: uuid.UUID
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankResponse:
    """Normalized reranker output."""

    results: list[RerankResult]
    provider: str
    model: str
    provider_version: str
    score_scale: RerankScoreScale
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None


class BaseRerankerProvider(ABC):
    """Rerank fused candidates behind a vendor-neutral interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""

    @property
    @abstractmethod
    def provider_version(self) -> str:
        """Provider implementation version."""

    @property
    def is_passthrough(self) -> bool:
        """True when fused order is preserved without a scoring round-trip."""
        return False

    @abstractmethod
    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Return candidates reordered by relevance."""
