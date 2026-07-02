"""In-memory vector store for tests and local development."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from app.platform.providers.contracts.vector_store import (
    BaseVectorStoreProvider,
    VectorPoint,
    VectorSearchFilter,
    VectorSearchResult,
)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


@dataclass
class _StoredPoint:
    vector: list[float]
    payload: dict[str, object]


class MemoryVectorStoreProvider(BaseVectorStoreProvider):
    """Process-local vector store backed by a dictionary."""

    def __init__(self) -> None:
        self._points: dict[str, _StoredPoint] = {}
        self._dimensions: int | None = None

    @property
    def provider_name(self) -> str:
        return "memory"

    async def ensure_collection(self, *, dimensions: int) -> None:
        self._dimensions = dimensions

    async def upsert_points(self, points: list[VectorPoint]) -> None:
        for point in points:
            self._points[point.point_id] = _StoredPoint(
                vector=list(point.vector),
                payload=dict(point.payload),
            )

    async def delete_by_document(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        project_value = str(project_id)
        document_value = str(document_id)
        to_delete = [
            point_id
            for point_id, stored in self._points.items()
            if stored.payload.get("project_id") == project_value
            and stored.payload.get("document_id") == document_value
        ]
        for point_id in to_delete:
            del self._points[point_id]

    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: VectorSearchFilter,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        project_value = str(filters.project_id)
        document_value = str(filters.document_id) if filters.document_id else None
        hits: list[VectorSearchResult] = []
        for point_id, stored in self._points.items():
            if stored.payload.get("project_id") != project_value:
                continue
            if document_value is not None and stored.payload.get("document_id") != document_value:
                continue
            if (
                filters.embedding_set_version is not None
                and stored.payload.get("embedding_set_version") != filters.embedding_set_version
            ):
                continue
            if not all(stored.payload.get(key) == value for key, value in filters.metadata.items()):
                continue
            score = _cosine_similarity(query_vector, stored.vector)
            if score_threshold is not None and score < score_threshold:
                continue
            hits.append(
                VectorSearchResult(
                    point_id=point_id,
                    score=score,
                    payload=dict(stored.payload),
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def clear(self) -> None:
        self._points.clear()
