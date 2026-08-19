"""Chunk validation before persistence."""

from __future__ import annotations

from app.core.config import ChunkingConfig
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService


class ChunkValidationService:
    """Validate and normalize draft chunks while preserving logical order."""

    def __init__(
        self,
        *,
        token_counter: TokenCountingService | None = None,
    ) -> None:
        self._token_counter = token_counter or TokenCountingService()

    def validate(self, chunks: list[DraftChunk], config: ChunkingConfig) -> list[DraftChunk]:
        validated = self._remove_empty(chunks)
        validated = self._remove_exact_duplicates(validated)
        validated = self._merge_tiny_chunks(validated, config)
        validated = self._prevent_isolated_headings(validated)
        validated = self._split_oversized(validated, config)
        validated = self._merge_tiny_chunks(validated, config)
        validated = self._remove_empty(validated)
        return self._reindex(validated)

    def _remove_empty(self, chunks: list[DraftChunk]) -> list[DraftChunk]:
        return [chunk for chunk in chunks if chunk.content.strip()]

    def _remove_exact_duplicates(self, chunks: list[DraftChunk]) -> list[DraftChunk]:
        seen: set[str] = set()
        unique: list[DraftChunk] = []
        for chunk in chunks:
            normalized = chunk.content.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(chunk)
        return unique

    def _merge_tiny_chunks(
        self, chunks: list[DraftChunk], config: ChunkingConfig
    ) -> list[DraftChunk]:
        if not chunks:
            return []
        if len(chunks) == 1:
            return chunks

        merged = list(chunks)
        index = 0
        while index < len(merged):
            if len(merged) == 1:
                break
            chunk = merged[index]
            if self._token_counter.count(chunk.content) >= config.min_tokens:
                index += 1
                continue
            if index == 0:
                merged[0:2] = [self._merge_adjacent(chunk, merged[1])]
                continue
            if index == len(merged) - 1:
                merged[index - 1 : index + 1] = [self._merge_adjacent(merged[index - 1], chunk)]
                index = max(index - 1, 0)
                continue

            left = merged[index - 1]
            right = merged[index + 1]
            forward = self._merge_adjacent(chunk, right)
            backward = self._merge_adjacent(left, chunk)
            forward_fits = self._token_counter.count(forward.content) <= config.max_tokens
            backward_fits = self._token_counter.count(backward.content) <= config.max_tokens
            prefer_forward = self._is_identifier_fragment(chunk)
            if forward_fits and (prefer_forward or not backward_fits):
                merged[index : index + 2] = [forward]
                continue
            if backward_fits:
                merged[index - 1 : index + 1] = [backward]
                index = max(index - 1, 0)
                continue
            if forward_fits:
                merged[index : index + 2] = [forward]
                continue
            if self._token_counter.count(left.content) <= self._token_counter.count(right.content):
                merged[index - 1 : index + 1] = [backward]
                index = max(index - 1, 0)
            else:
                merged[index : index + 2] = [forward]
        return merged

    def _is_identifier_fragment(self, chunk: DraftChunk) -> bool:
        text = chunk.content.strip()
        return self._token_counter.count(text) <= 4 and len(text) <= 16

    def _prevent_isolated_headings(self, chunks: list[DraftChunk]) -> list[DraftChunk]:
        if len(chunks) < 2:
            return chunks

        result: list[DraftChunk] = []
        index = 0
        while index < len(chunks):
            chunk = chunks[index]
            is_heading_only = (
                chunk.heading_level is not None and self._token_counter.count(chunk.content) <= 12
            )
            if is_heading_only and index + 1 < len(chunks):
                merged = self._merge_adjacent(chunk, chunks[index + 1])
                result.append(merged)
                index += 2
                continue
            result.append(chunk)
            index += 1
        return result

    def _split_oversized(
        self, chunks: list[DraftChunk], config: ChunkingConfig
    ) -> list[DraftChunk]:
        from app.modules.knowledge.services.chunking.chunk_strategies import (
            RecursiveFallbackChunkStrategy,
        )

        fallback = RecursiveFallbackChunkStrategy(token_counter=self._token_counter)
        result: list[DraftChunk] = []
        for chunk in chunks:
            if self._token_counter.count(chunk.content) <= config.max_tokens:
                result.append(chunk)
                continue
            split_chunks = fallback.split_text(
                chunk.content,
                config=config,
                base_metadata={
                    **chunk.metadata,
                    "split_reason": "validation_max_tokens",
                },
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_title,
                heading_level=chunk.heading_level,
            )
            result.extend(split_chunks)
        return result

    def _merge_adjacent(self, left: DraftChunk, right: DraftChunk) -> DraftChunk:
        separator = "\n\n" if left.content.strip() and right.content.strip() else ""
        return DraftChunk(
            content=f"{left.content.strip()}{separator}{right.content.strip()}".strip(),
            char_start=left.char_start,
            char_end=right.char_end,
            page_start=left.page_start or right.page_start,
            page_end=right.page_end or left.page_end,
            section_title=left.section_title or right.section_title,
            heading_level=left.heading_level or right.heading_level,
            chunk_order=left.chunk_order,
            metadata={**left.metadata, **right.metadata, "merged": True},
        )

    def _reindex(self, chunks: list[DraftChunk]) -> list[DraftChunk]:
        for index, chunk in enumerate(chunks):
            chunk.chunk_order = index
        return chunks
