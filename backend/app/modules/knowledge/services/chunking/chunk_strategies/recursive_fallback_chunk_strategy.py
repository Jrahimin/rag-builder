"""Recursive token-based fallback splitter."""

from __future__ import annotations

from app.core.config import ChunkingConfig
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService


class RecursiveFallbackChunkStrategy:
    """Split oversized text by paragraphs, lines, then words."""

    def __init__(self, *, token_counter: TokenCountingService | None = None) -> None:
        self._token_counter = token_counter or TokenCountingService()

    def split_text(
        self,
        text: str,
        *,
        config: ChunkingConfig,
        base_metadata: dict | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        section_title: str | None = None,
        heading_level: int | None = None,
    ) -> list[DraftChunk]:
        metadata = dict(base_metadata or {})
        metadata["strategy_used"] = "recursive_fallback"
        separators = ["\n\n", "\n", " "]
        parts = self._split_recursive(text, separators, config.max_tokens)
        chunks: list[DraftChunk] = []
        cursor = 0
        for part in parts:
            start = text.find(part, cursor)
            if start < 0:
                start = cursor
            end = start + len(part)
            cursor = end
            chunks.append(
                DraftChunk(
                    content=part,
                    char_start=start,
                    char_end=end,
                    page_start=page_start,
                    page_end=page_end,
                    section_title=section_title,
                    heading_level=heading_level,
                    metadata=metadata,
                )
            )
        return chunks

    def _split_recursive(self, text: str, separators: list[str], max_tokens: int) -> list[str]:
        if self._token_counter.count(text) <= max_tokens:
            return [text] if text.strip() else []
        if not separators:
            return self._split_by_tokens(text, max_tokens)
        separator = separators[0]
        parts = text.split(separator)
        if len(parts) == 1:
            return self._split_recursive(text, separators[1:], max_tokens)
        result: list[str] = []
        buffer = ""
        for part in parts:
            candidate = part if not buffer else f"{buffer}{separator}{part}"
            if self._token_counter.count(candidate) <= max_tokens:
                buffer = candidate
                continue
            if buffer:
                result.append(buffer)
            if self._token_counter.count(part) > max_tokens:
                result.extend(self._split_recursive(part, separators[1:], max_tokens))
                buffer = ""
            else:
                buffer = part
        if buffer:
            result.append(buffer)
        return result

    def _split_by_tokens(self, text: str, max_tokens: int) -> list[str]:
        words = text.split()
        if not words:
            return []
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word]).strip()
            if self._token_counter.count(candidate) > max_tokens and current:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
        return chunks
