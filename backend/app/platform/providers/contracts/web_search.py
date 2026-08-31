"""Vendor-neutral external web-search contract and evidence DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class WebDiscoveredSource:
    """Provider-neutral identity for a source discovered by web search."""

    provider_id: str | None
    title: str
    original_url: str
    canonical_url: str


@dataclass(frozen=True, slots=True)
class WebEvidence:
    """Bounded source-owned result text with a conservative source association."""

    evidence_id: str
    title: str
    url: str
    content: str
    retrieved_at: datetime
    citation_verified: bool = False
    source_id: str | None = None
    canonical_url: str | None = None


# Compatibility name retained for existing provider fakes and downstream imports.
WebSearchEvidence = WebEvidence


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Normalized external search result with auditable provider diagnostics."""

    evidence: list[WebSearchEvidence]
    provider: str
    model: str
    provider_version: str
    diagnostics: dict[str, Any] = field(default_factory=dict)
    discovered_sources: list[WebDiscoveredSource] = field(default_factory=list)


class BaseWebSearchProvider(ABC):
    """Retrieve current external evidence without leaking vendor response types."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def provider_version(self) -> str: ...

    @abstractmethod
    async def search(self, query: str, *, max_results: int) -> WebSearchResult: ...
