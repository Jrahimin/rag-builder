"""Budget trimming for retrieved context chunks."""

from __future__ import annotations

import uuid

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk


class ContextBuilder:
    """Dedupe and trim already-ranked chunks; does not re-sort or build citations."""

    def __init__(self, config: ChatConfig) -> None:
        self._config = config

    def select(self, chunks: list[ContextChunk]) -> list[ContextChunk]:
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        selected: list[ContextChunk] = []
        char_budget = self._config.context_char_budget

        for chunk in chunks:
            if chunk.chunk_id in seen_ids or chunk.chunk_hash in seen_hashes:
                continue
            seen_ids.add(chunk.chunk_id)
            seen_hashes.add(chunk.chunk_hash)
            if len(selected) >= self._config.max_context_chunks:
                break
            if char_budget <= 0:
                break
            if len(chunk.content) > char_budget:
                trimmed = ContextChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content[:char_budget],
                    score=chunk.score,
                    filename=chunk.filename,
                    chunk_hash=chunk.chunk_hash,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata=chunk.metadata,
                )
                selected.append(trimmed)
                char_budget = 0
            else:
                selected.append(chunk)
                char_budget -= len(chunk.content)

        return selected
