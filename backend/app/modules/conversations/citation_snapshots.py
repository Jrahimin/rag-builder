"""Map selected chunks to durable citation snapshots."""

from __future__ import annotations

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import CitationSnapshot


def build_citation_snapshots(
    chunks: list[ContextChunk],
    *,
    config: ChatConfig,
) -> list[dict]:
    """Build JSON-serializable citation snapshots for assistant message persistence."""
    snapshots: list[dict] = []
    max_excerpt = config.citation_excerpt_max_chars
    for chunk in chunks:
        excerpt: str | None = None
        if max_excerpt > 0:
            excerpt = chunk.content[:max_excerpt]
        snapshot = CitationSnapshot(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            score=chunk.score,
            chunk_hash=chunk.chunk_hash,
            excerpt=excerpt,
        )
        snapshots.append(snapshot.model_dump(mode="json"))
    return snapshots
