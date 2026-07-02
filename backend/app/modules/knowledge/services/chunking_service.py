"""Text chunking for knowledge ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter, TextSplitter

from app.core.config import ChunkingConfig, ChunkingStrategy, Settings


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A single split segment ready for persistence."""

    content: str
    chunk_index: int
    char_start: int
    char_end: int
    page_number: int | None
    token_count: int
    chunk_metadata: dict[str, Any]


def estimate_token_count(text: str) -> int:
    """Rough token estimate without a model tokenizer (words-based)."""
    stripped = text.strip()
    if not stripped:
        return 0
    return len(stripped.split())


def _build_splitter(
    strategy: ChunkingStrategy,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> TextSplitter:
    """Map the configured strategy to a concrete splitter."""
    if strategy is ChunkingStrategy.RECURSIVE_CHARACTER:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    msg = f"Unsupported chunking strategy: {strategy!r}"
    raise ValueError(msg)


class ChunkingService:
    """Split normalized document text using the configured strategy."""

    def __init__(
        self,
        *,
        chunk_size: int,
        chunk_overlap: int,
        strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE_CHARACTER,
    ) -> None:
        self._chunk_overlap = chunk_overlap
        self._strategy = strategy
        self._splitter = _build_splitter(
            strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> ChunkingService:
        config: ChunkingConfig = settings.chunking
        return cls(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            strategy=config.strategy,
        )

    def split(self, text: str, *, page_count: int | None = None) -> list[TextChunk]:
        if not text.strip():
            return []

        parts = self._splitter.split_text(text)
        default_page = 1 if page_count == 1 else None
        chunks: list[TextChunk] = []
        search_from = 0

        for index, part in enumerate(parts):
            start = text.find(part, search_from)
            if start < 0:
                start = search_from
            end = start + len(part)
            search_from = max(0, end - self._chunk_overlap)

            chunks.append(
                TextChunk(
                    content=part,
                    chunk_index=index,
                    char_start=start,
                    char_end=end,
                    page_number=default_page,
                    token_count=estimate_token_count(part),
                    chunk_metadata={"splitter": self._strategy.value},
                )
            )

        return chunks
