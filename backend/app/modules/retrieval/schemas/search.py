"""Search schemas and stable retrieval DTOs."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import RetrievalStrategy


class SearchRequest(BaseModel):
    """Search request body."""

    query: str = Field(min_length=1, max_length=4096)
    top_k: int | None = Field(default=None, ge=1, le=100)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    strategy: RetrievalStrategy | None = None
    rerank: bool | None = None


class RetrievalResult(BaseModel):
    """Stable search hit DTO for API and future Chat integration."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float
    filename: str
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchDiagnostics(BaseModel):
    """Sanitized execution facts used by quality evaluation and operators."""

    strategy: RetrievalStrategy
    duration_ms: int
    rerank_requested: bool
    rerank_status: str
    reranker_provider: str | None = None
    reranker_model: str | None = None
    reranker_version: str | None = None
    duplicate_suppression_input_count: int = 0
    duplicate_suppression_removed_count: int = 0
    duplicate_suppression_reasons: dict[str, int] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Search response wrapper."""

    results: list[RetrievalResult]
    query: str
    top_k: int
    diagnostics: SearchDiagnostics = Field(
        default_factory=lambda: SearchDiagnostics(
            strategy=RetrievalStrategy.SEMANTIC,
            duration_ms=0,
            rerank_requested=False,
            rerank_status="not_recorded",
        )
    )
