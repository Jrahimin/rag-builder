"""Budget trimming for retrieved context chunks."""

from __future__ import annotations

import uuid
from dataclasses import replace

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
                selected.append(
                    _preserve_rerank_score(replace(chunk, content=chunk.content[:char_budget]))
                )
                char_budget = 0
            else:
                selected.append(_preserve_rerank_score(chunk))
                char_budget -= len(chunk.content)

        return selected


def _preserve_rerank_score(chunk: ContextChunk) -> ContextChunk:
    """Keep Cohere relevance on the selected chunk when provenance dropped the field."""
    if chunk.rerank_relevance_score is not None:
        return chunk
    if str(chunk.metadata.get("rerank_status")) != "applied" or chunk.score <= 0.0:
        return chunk
    return replace(
        chunk,
        rerank_relevance_score=chunk.score,
        rank_score=chunk.rank_score if chunk.rank_score is not None else chunk.score,
        evidence_relevance_score=(
            chunk.evidence_relevance_score
            if chunk.evidence_relevance_score is not None
            else chunk.score
        ),
        evidence_score_method=chunk.evidence_score_method or "reranker_relevance",
    )
