"""Retrieval port and neutral context DTOs for chat."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ContextChunk:
    """Already-ranked chunk from retrieval, ready for context budgeting."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    score: float
    filename: str
    chunk_hash: str
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RetrievalPort(Protocol):
    """Project-scoped retrieval seam — ranking owned by the adapter implementation."""

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[ContextChunk]: ...
