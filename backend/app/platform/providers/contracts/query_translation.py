"""Query-translation provider contract and neutral DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class QueryTranslationRequest:
    """One retrieval-only translation request."""

    query: str
    source_profile: str
    target_language: str
    prompt_version: str
    max_output_tokens: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueryTranslationUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class QueryTranslationResponse:
    """Translated retrieval query plus provider provenance. Never evidence."""

    translated_query: str
    provider: str
    model: str
    provider_version: str
    prompt_version: str
    usage: QueryTranslationUsage = field(default_factory=QueryTranslationUsage)
    latency_ms: int | None = None


class BaseQueryTranslationProvider(ABC):
    """Translate a user query for retrieval without answering it."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def provider_version(self) -> str: ...

    @property
    @abstractmethod
    def prompt_version(self) -> str: ...

    @abstractmethod
    async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
        """Return a faithful retrieval translation or raise ProviderError."""
