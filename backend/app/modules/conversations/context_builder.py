"""Budget trimming for retrieved context chunks."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk, EvidenceUnit


class ContextBuilder:
    """Dedupe and trim already-ranked chunks; does not re-sort or build citations."""

    def __init__(self, config: ChatConfig) -> None:
        self._config = config

    def select(self, chunks: Sequence[ContextChunk]) -> list[ContextChunk]:
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
            if isinstance(chunk, EvidenceUnit) and len(chunk.content) > char_budget:
                # An admitted unit is indivisible: budgeting may omit it, never rewrite it.
                continue
            chunk = chunk.restore_applied_rerank_scores()
            if len(chunk.content) > char_budget:
                selected.append(replace(chunk, content=chunk.content[:char_budget]))
                char_budget = 0
            else:
                selected.append(chunk)
                char_budget -= len(chunk.content)

        return selected
