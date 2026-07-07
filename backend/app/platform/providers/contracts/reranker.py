"""Reranker provider contract and neutral DTOs."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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

    @abstractmethod
    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """Return candidates reordered by relevance."""
