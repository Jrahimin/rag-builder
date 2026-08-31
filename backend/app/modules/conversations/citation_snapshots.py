"""Map selected chunks to durable citation snapshots."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import ChatConfig
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import CitationSnapshot, CitationSourceKind


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
        is_web = chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value
        snapshot = CitationSnapshot(
            source_kind=(CitationSourceKind.WEB if is_web else CitationSourceKind.KNOWLEDGE),
            chunk_id=None if is_web else chunk.chunk_id,
            project_id=None if is_web else project_id,
            document_id=None if is_web else chunk.document_id,
            filename=chunk.filename,
            chunk_index=None if is_web else chunk.chunk_index,
            page_number=None if is_web else chunk.page_number,
            char_start=None if is_web else chunk.char_start,
            char_end=None if is_web else chunk.char_end,
            score=None if is_web else chunk.score,
            chunk_hash=None if is_web else chunk.chunk_hash,
            evidence_unit_id=(
                None if is_web else chunk.metadata.get("evidence_unit_id")
            ),
            evidence_span_hash=(
                None if is_web else chunk.metadata.get("evidence_span_hash")
            ),
            evidence_chunk_char_start=(
                None if is_web else chunk.metadata.get("evidence_chunk_char_start")
            ),
            evidence_chunk_char_end=(
                None if is_web else chunk.metadata.get("evidence_chunk_char_end")
            ),
            evidence_span_derivation=(
                None if is_web else chunk.metadata.get("evidence_span_derivation")
            ),
            evidence_query_variant_id=(
                None if is_web else chunk.metadata.get("evidence_query_variant_id")
            ),
            excerpt=excerpt,
            processing_version=None if is_web else chunk.metadata.get("processing_version"),
            index_build_id=None if is_web else chunk.metadata.get("index_build_id"),
            source_metadata_generation=(
                None if is_web else chunk.metadata.get("source_metadata_generation")
            ),
            source_revision_id=None if is_web else chunk.metadata.get("source_revision_id"),
            source_group_id=None if is_web else chunk.metadata.get("source_group_id"),
            source_title=None if is_web else chunk.metadata.get("source_title"),
            source_type=None if is_web else chunk.metadata.get("source_type"),
            source_revision_number=(
                None if is_web else chunk.metadata.get("source_revision_number")
            ),
            source_revision_label=None if is_web else chunk.metadata.get("source_revision_label"),
            source_published_date=None if is_web else chunk.metadata.get("source_published_date"),
            source_effective_from=None if is_web else chunk.metadata.get("source_effective_from"),
            source_effective_to=None if is_web else chunk.metadata.get("source_effective_to"),
            source_lifecycle_status=None
            if is_web
            else chunk.metadata.get("source_lifecycle_status"),
            source_role=None if is_web else chunk.metadata.get("source_role"),
            source_relationships=(
                [] if is_web else list(chunk.metadata.get("source_relationships") or [])
            ),
            relationship_recall_provenance=(
                []
                if is_web
                else list(chunk.metadata.get("relationship_recall_provenance") or [])
            ),
            config_snapshot_id=config_snapshot_id,
            configuration_hash=chunk.metadata.get("configuration_hash"),
            config_provenance=config_provenance,
            prompt_version=prompt_version,
            web_url=chunk.metadata.get("web_url") if is_web else None,
            web_title=chunk.metadata.get("web_title") if is_web else None,
            web_retrieved_at=chunk.metadata.get("web_retrieved_at") if is_web else None,
            web_provider=chunk.metadata.get("web_provider") if is_web else None,
        )
        snapshots.append(snapshot.model_dump(mode="json"))
    return snapshots
