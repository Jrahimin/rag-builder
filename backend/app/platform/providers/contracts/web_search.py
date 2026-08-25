"""Vendor-neutral external web-search contract and evidence DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class WebSearchEvidence:
    """A bounded, cited excerpt returned by an external search provider."""

    evidence_id: str
    title: str
    url: str
    content: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Normalized external search result with auditable provider diagnostics."""

    evidence: list[WebSearchEvidence]
    provider: str
    model: str
    provider_version: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


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
