"""Vector store provider contract and neutral DTOs."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """A single vector point to upsert in the store."""

    point_id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """Neutral search hit from a vector store."""

    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorSearchFilter:
    """Project-scoped search filter with optional metadata constraints."""

    project_id: uuid.UUID
    document_id: uuid.UUID | None = None
    embedding_set_version: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class BaseVectorStoreProvider(ABC):
    """Upsert and search vectors behind a vendor-neutral interface."""

    @abstractmethod
    async def ensure_collection(self, *, dimensions: int) -> None:
        """Create the collection if it does not exist."""

    @abstractmethod
    async def upsert_points(self, points: list[VectorPoint]) -> None:
        """Insert or replace vector points."""

    @abstractmethod
    async def delete_by_document(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """Remove all points for a document within a project."""

    @abstractmethod
    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: VectorSearchFilter,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        """Return nearest neighbors matching filters."""
