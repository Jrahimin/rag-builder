"""Map selected chunks to durable citation snapshots."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import ChatConfig
from app.modules.conversations.current_authority import (
    IRRELEVANT_AUTHORITY_OUTCOMES,
    authority_record_affects_chunk,
)
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import CitationSnapshot, CitationSourceKind

EVIDENCE_PROVENANCE_VERSION = "citation_reuse.v2"
REUSE_VALIDATION_VERSION = "presentation_reuse.v2"


def build_citation_snapshots(
    chunks: list[ContextChunk],
    *,
    config: ChatConfig,
    project_id: uuid.UUID,
    config_snapshot_id: uuid.UUID | None,
    config_provenance: dict[str, Any],
    prompt_version: str,
    evidence_scope: dict[str, Any] | None = None,
    originating_assistant_message_id: uuid.UUID | None = None,
    coverage_origin_message_id: uuid.UUID | None = None,
    coverage_status: str | None = None,
    coverage_partial: bool | None = None,
    expansion_records: list[dict[str, Any]] | None = None,
    recalled_chunks: list[ContextChunk] | None = None,
) -> list[dict]:
    """Build JSON-serializable citation snapshots for assistant message persistence."""
    snapshots: list[dict] = []
    max_excerpt = config.citation_excerpt_max_chars
    scope = evidence_scope or {}
    records = [item for item in expansion_records or [] if isinstance(item, dict)]
    recalled = recalled_chunks if recalled_chunks is not None else chunks
    for chunk in chunks:
        excerpt: str | None = None
        if max_excerpt > 0:
            excerpt = chunk.content[:max_excerpt]
        is_web = chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value
        reconstructed = _reconstructed_source_envelope(chunk.metadata)
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
            evidence_unit_id=(None if is_web else chunk.metadata.get("evidence_unit_id")),
            evidence_span_hash=(None if is_web else chunk.metadata.get("evidence_span_hash")),
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
            authority_status=chunk.metadata.get("authority_status"),
            authority_limitations=list(chunk.metadata.get("authority_limitations") or []),
            source_relationships=(
                [] if is_web else list(chunk.metadata.get("source_relationships") or [])
            ),
            relationship_recall_provenance=(
                [] if is_web else list(chunk.metadata.get("relationship_recall_provenance") or [])
            ),
            authority_dependencies=(
                [] if is_web else _authority_dependencies_for(chunk, records, recalled)
            ),
            config_snapshot_id=config_snapshot_id,
            configuration_hash=chunk.metadata.get("configuration_hash"),
            config_provenance=config_provenance,
            prompt_version=prompt_version,
            web_url=chunk.metadata.get("web_url") if is_web else None,
            web_title=chunk.metadata.get("web_title") if is_web else None,
            web_retrieved_at=chunk.metadata.get("web_retrieved_at") if is_web else None,
            web_provider=chunk.metadata.get("web_provider") if is_web else None,
            evidence_provenance_version=None if is_web else EVIDENCE_PROVENANCE_VERSION,
            indexed_chunk_hash=None if is_web else chunk.metadata.get("indexed_chunk_hash"),
            evidence_source_chunk_hash=(
                None
                if is_web
                else chunk.metadata.get("evidence_source_chunk_hash")
                or getattr(chunk, "source_chunk_hash", None)
                or None
            ),
            evidence_corroboration_method=(
                None
                if is_web
                else chunk.metadata.get("evidence_corroboration_method")
                or getattr(chunk, "corroboration_method", None)
                or None
            ),
            evidence_source_envelope=None if is_web else reconstructed,
            evidence_scope_document_id=(
                None if is_web else _optional_uuid(scope.get("document_id"))
            ),
            evidence_scope_metadata_filter=(
                None if is_web else dict(scope.get("metadata_filter") or {})
            ),
            evidence_scope_as_of=None if is_web else scope.get("as_of"),
            evidence_scope_snapshot_origin=(None if is_web else scope.get("snapshot_origin")),
            originating_assistant_message_id=(
                None
                if is_web
                else _optional_uuid(chunk.metadata.get("originating_assistant_message_id"))
                or originating_assistant_message_id
            ),
            coverage_origin_message_id=None if is_web else coverage_origin_message_id,
            coverage_status=None if is_web else coverage_status,
            coverage_partial=None if is_web else coverage_partial,
        )
        snapshots.append(snapshot.model_dump(mode="json"))
    return snapshots


def citation_authority_records(citation: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer persisted authority_dependencies; fall back to legacy recall provenance."""
    stored = citation.get("authority_dependencies")
    if isinstance(stored, list):
        return [item for item in stored if isinstance(item, dict)]
    return [
        item
        for item in citation.get("relationship_recall_provenance") or []
        if isinstance(item, dict)
    ]


def _authority_dependencies_for(
    chunk: ContextChunk,
    records: list[dict[str, Any]],
    recalled_chunks: list[ContextChunk],
) -> list[dict[str, Any]]:
    present_revisions = {
        str(value)
        for item in recalled_chunks
        if (value := item.metadata.get("source_revision_id")) is not None
    }
    chunks_by_revision: dict[str, list[str]] = {}
    for item in recalled_chunks:
        revision = str(item.metadata.get("source_revision_id") or "")
        if not revision:
            continue
        identifier = str(item.chunk_id)
        known = chunks_by_revision.setdefault(revision, [])
        if identifier not in known:
            known.append(identifier)
    dependencies: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("outcome") or "") in IRRELEVANT_AUTHORITY_OUTCOMES:
            continue
        if not authority_record_affects_chunk(record, chunk):
            continue
        dependency = dict(record)
        modifier = str(record.get("modifier_revision_id") or "")
        dependency["modifier_recalled"] = bool(modifier and modifier in present_revisions)
        recalled_ids = chunks_by_revision.get(modifier) or []
        if recalled_ids:
            dependency["modifier_chunk_id"] = recalled_ids[0]
            if len(recalled_ids) > 1:
                dependency["modifier_chunk_ids"] = list(recalled_ids)
        dependencies.append(dependency)
    return dependencies


def _reconstructed_source_envelope(metadata: dict[str, Any]) -> str:
    if (
        metadata.get("table_context")
        or metadata.get("table_row_group")
        or metadata.get("heading_context_status") == "preserved"
        or metadata.get("evidence_source_envelope") == "reconstructed_context"
    ):
        return "reconstructed_context"
    return "contiguous_span"


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
