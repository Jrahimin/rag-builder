"""Map selected chunks to durable citation snapshots."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import CitationSnapshot


def build_citation_snapshots(
    chunks: list[ContextChunk],
    *,
    config: ChatConfig,
    project_id: uuid.UUID,
    config_snapshot_id: uuid.UUID | None,
    config_provenance: dict[str, Any],
    prompt_version: str,
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
            project_id=project_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            score=chunk.score,
            chunk_hash=chunk.chunk_hash,
            excerpt=excerpt,
            processing_version=chunk.metadata.get("processing_version"),
            index_build_id=chunk.metadata.get("index_build_id"),
            source_metadata_generation=chunk.metadata.get("source_metadata_generation"),
            source_revision_id=chunk.metadata.get("source_revision_id"),
            source_group_id=chunk.metadata.get("source_group_id"),
            source_title=chunk.metadata.get("source_title"),
            source_type=chunk.metadata.get("source_type"),
            source_revision_number=chunk.metadata.get("source_revision_number"),
            source_revision_label=chunk.metadata.get("source_revision_label"),
            source_published_date=chunk.metadata.get("source_published_date"),
            source_effective_from=chunk.metadata.get("source_effective_from"),
            source_effective_to=chunk.metadata.get("source_effective_to"),
            source_lifecycle_status=chunk.metadata.get("source_lifecycle_status"),
            source_role=chunk.metadata.get("source_role"),
            source_relationships=list(chunk.metadata.get("source_relationships") or []),
            config_snapshot_id=config_snapshot_id,
            configuration_hash=chunk.metadata.get("configuration_hash"),
            config_provenance=config_provenance,
            prompt_version=prompt_version,
        )
        snapshots.append(snapshot.model_dump(mode="json"))
    return snapshots
