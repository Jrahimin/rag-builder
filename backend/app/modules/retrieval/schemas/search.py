"""Search schemas and stable retrieval DTOs."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Semantic search request body."""

    query: str = Field(min_length=1, max_length=4096)
    top_k: int | None = Field(default=None, ge=1, le=100)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)


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


class SearchResponse(BaseModel):
    """Search response wrapper."""

    results: list[RetrievalResult]
    query: str
    top_k: int
