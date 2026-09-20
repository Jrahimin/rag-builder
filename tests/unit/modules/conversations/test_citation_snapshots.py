"""Unit tests for citation snapshot builder."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import (
    EVIDENCE_PROVENANCE_VERSION,
    build_citation_snapshots,
)
from app.modules.conversations.ports import ContextChunk, EvidenceUnit
from app.modules.conversations.schemas.message import CitationSnapshot, CitationSourceKind
from app.platform.domain.content_hash import content_hash

pytestmark = pytest.mark.unit


def test_build_citation_snapshots_includes_hash_and_excerpt() -> None:
    project_id = uuid.uuid4()
    index_build_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    group_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    document_id = uuid.uuid4()
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id,
        chunk_index=0,
        content="x" * 300,
        score=0.9,
        filename="doc.txt",
        chunk_hash="deadbeef",
        metadata={
            "index_build_id": str(index_build_id),
            "source_metadata_generation": 3,
            "source_revision_id": str(revision_id),
            "source_group_id": str(group_id),
            "source_title": "Durable policy",
            "source_lifecycle_status": "active",
            "source_role": "primary",
            "source_effective_from": date(2026, 1, 1).isoformat(),
            "source_relationships": [
                {
                    "relationship_type": "replaces",
                    "target_revision_id": str(uuid.uuid4()),
                    "direction": "incoming",
                }
            ],
            "relationship_recall_provenance": [
                {
                    "relationship_type": "modifies",
                    "depth": 1,
                    "base_revision_id": str(uuid.uuid4()),
                    "base_document_id": str(uuid.uuid4()),
                    "modifier_revision_id": str(revision_id),
                    "modifier_document_id": str(document_id),
                }
            ],
            "configuration_hash": "a" * 64,
        },
    )
    snapshots = build_citation_snapshots(
        [chunk],
        project_id=project_id,
        config_snapshot_id=snapshot_id,
        config_provenance={"source_metadata_generation": 3},
        prompt_version="v1",
        config=ChatConfig(citation_excerpt_max_chars=50),
    )
    assert len(snapshots) == 1
    assert snapshots[0]["chunk_hash"] == "deadbeef"
    assert len(snapshots[0]["excerpt"]) == 50
    assert snapshots[0]["source_metadata_generation"] == 3
    assert snapshots[0]["prompt_version"] == "v1"
    assert snapshots[0]["project_id"] == str(project_id)
    assert snapshots[0]["index_build_id"] == str(index_build_id)
    assert snapshots[0]["source_revision_id"] == str(revision_id)
    assert snapshots[0]["source_group_id"] == str(group_id)
    assert snapshots[0]["config_snapshot_id"] == str(snapshot_id)
    assert snapshots[0]["configuration_hash"] == "a" * 64
    assert snapshots[0]["source_relationships"][0]["relationship_type"] == "replaces"
    assert snapshots[0]["source_relationships"][0]["direction"] == "incoming"
    assert snapshots[0]["relationship_recall_provenance"][0]["relationship_type"] == ("modifies")


def test_web_snapshot_exposes_only_web_source_identity() -> None:
    chunk = ContextChunk(
        # These are internal transport identifiers only; they must not reach citation clients.
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Refund requests are accepted within 30 days.",
        score=0.0,
        filename="Refund policy",
        chunk_hash="web-source",
        metadata={
            "source_kind": "web",
            "web_url": "https://example.test/refunds",
            "web_title": "Refund policy",
            "web_retrieved_at": datetime.now(UTC).isoformat(),
            "web_provider": "test_web",
        },
    )

    snapshot = build_citation_snapshots(
        [chunk],
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v5",
        config=ChatConfig(),
    )[0]

    assert snapshot["source_kind"] == "web"
    assert snapshot["chunk_id"] is None
    assert snapshot["document_id"] is None
    assert snapshot["project_id"] is None
    assert snapshot["chunk_index"] is None
    assert snapshot["web_url"] == "https://example.test/refunds"


def test_base_and_modifier_keep_distinct_citation_identity() -> None:
    base_document_id = uuid.uuid4()
    base_revision_id = uuid.uuid4()
    modifier_document_id = uuid.uuid4()
    modifier_revision_id = uuid.uuid4()
    base = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=base_document_id,
        chunk_index=0,
        content="Base authority.",
        score=0.9,
        filename="base.txt",
        chunk_hash="base-hash",
        metadata={
            "source_revision_id": str(base_revision_id),
            "source_title": "Base authority",
            "source_effective_from": "2025-01-01",
        },
    )
    modifier = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=modifier_document_id,
        chunk_index=0,
        content="Modifying authority.",
        score=0.8,
        filename="modifier.txt",
        chunk_hash="modifier-hash",
        metadata={
            "source_revision_id": str(modifier_revision_id),
            "source_title": "Modifying authority",
            "source_effective_from": "2026-01-01",
            "relationship_recall_provenance": [
                {
                    "relationship_type": "modifies",
                    "depth": 1,
                    "base_revision_id": str(base_revision_id),
                    "base_document_id": str(base_document_id),
                    "modifier_revision_id": str(modifier_revision_id),
                    "modifier_document_id": str(modifier_document_id),
                }
            ],
        },
    )

    expansion_record = {
        "relationship_id": str(uuid.uuid4()),
        "relationship_type": "modifies",
        "base_revision_id": str(base_revision_id),
        "modifier_revision_id": str(modifier_revision_id),
        "outcome": "expanded",
        "target_provisions": ["Section 21"],
    }
    snapshots = build_citation_snapshots(
        [base, modifier],
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v5",
        config=ChatConfig(),
        expansion_records=[expansion_record],
    )

    assert [item["document_id"] for item in snapshots] == [
        str(base_document_id),
        str(modifier_document_id),
    ]
    assert [item["source_revision_id"] for item in snapshots] == [
        str(base_revision_id),
        str(modifier_revision_id),
    ]
    assert snapshots[0]["relationship_recall_provenance"] == []
    assert snapshots[1]["relationship_recall_provenance"][0]["base_revision_id"] == str(
        base_revision_id
    )
    assert snapshots[0]["authority_dependencies"][0]["modifier_revision_id"] == str(
        modifier_revision_id
    )
    assert snapshots[0]["authority_dependencies"][0]["modifier_recalled"] is True
    assert snapshots[0]["authority_dependencies"][0]["modifier_chunk_id"] == str(modifier.chunk_id)
    assert snapshots[1]["authority_dependencies"][0]["modifier_recalled"] is True
    assert snapshots[1]["authority_dependencies"][0]["modifier_chunk_id"] == str(modifier.chunk_id)


def test_unselected_recalled_modifier_is_recorded_without_becoming_a_citation() -> None:
    base_revision_id = uuid.uuid4()
    modifier_revision_id = uuid.uuid4()
    base = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Section 21 — Investment Rebate Rate\nThe rebate is 15%.",
        score=0.9,
        filename="base.txt",
        chunk_hash="base-hash",
        metadata={"source_revision_id": str(base_revision_id)},
    )
    modifier = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Section 5 — Amendment of section 21\nThe rebate is 10%.",
        score=0.8,
        filename="modifier.txt",
        chunk_hash="modifier-hash",
        metadata={"source_revision_id": str(modifier_revision_id)},
    )
    expansion_record = {
        "relationship_id": str(uuid.uuid4()),
        "relationship_type": "modifies",
        "base_revision_id": str(base_revision_id),
        "modifier_revision_id": str(modifier_revision_id),
        "outcome": "expanded",
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
    }
    stale = {
        **expansion_record,
        "relationship_id": str(uuid.uuid4()),
        "modifier_revision_id": str(uuid.uuid4()),
        "outcome": "stale_or_replaced_revision",
    }
    snapshots = build_citation_snapshots(
        [base],
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v5",
        config=ChatConfig(),
        expansion_records=[expansion_record, stale],
        recalled_chunks=[base, modifier],
    )
    assert len(snapshots) == 1
    dependencies = snapshots[0]["authority_dependencies"]
    assert [item["outcome"] for item in dependencies] == ["expanded"]
    assert dependencies[0]["modifier_recalled"] is True
    assert dependencies[0]["modifier_chunk_id"] == str(modifier.chunk_id)


def test_source_specific_citation_identity_is_enforced() -> None:
    with pytest.raises(ValidationError, match="web citations cannot expose internal"):
        CitationSnapshot(
            source_kind=CitationSourceKind.WEB,
            filename="Refund policy",
            chunk_id=uuid.uuid4(),
            web_url="https://example.test/refunds",
            web_title="Refund policy",
            web_retrieved_at=datetime.now(UTC),
            web_provider="test_web",
        )


def test_knowledge_snapshots_record_distinct_raw_and_span_hashes() -> None:
    content = "A customer may request a refund within 30 days of purchase."
    raw_hash = content_hash(content)
    span = content[10:40]
    span_hash = content_hash(span)
    origin = uuid.uuid4()
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    unit = EvidenceUnit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=span,
        score=0.9,
        filename="policy.txt",
        chunk_hash=span_hash,
        metadata={
            "indexed_chunk_hash": raw_hash,
            "evidence_source_chunk_hash": raw_hash,
            "evidence_span_hash": span_hash,
            "evidence_chunk_char_start": 10,
            "evidence_chunk_char_end": 40,
            "evidence_span_derivation": "complete_chunk",
            "evidence_corroboration_method": "original_lexical",
            "index_build_id": str(uuid.uuid4()),
            "source_metadata_generation": 2,
            "processing_version": 3,
            "configuration_hash": "b" * 64,
        },
        evidence_unit_id="unit-1",
        source_chunk_hash=raw_hash,
        evidence_span_hash=span_hash,
        evidence_char_start=10,
        evidence_char_end=40,
        span_derivation="complete_chunk",
        corroboration_method="original_lexical",
    )
    snapshot = build_citation_snapshots(
        [unit],
        project_id=uuid.uuid4(),
        config_snapshot_id=uuid.uuid4(),
        config_provenance={"project_config_revision_number": 9},
        prompt_version="v5",
        config=ChatConfig(),
        evidence_scope={
            "document_id": unit.document_id,
            "metadata_filter": {"region": "Dhaka"},
            "as_of": as_of,
            "snapshot_origin": "user_literal",
        },
        originating_assistant_message_id=origin,
        coverage_origin_message_id=origin,
        coverage_status="partial",
        coverage_partial=True,
    )[0]
    assert snapshot["evidence_provenance_version"] == EVIDENCE_PROVENANCE_VERSION
    assert snapshot["indexed_chunk_hash"] == raw_hash
    assert snapshot["evidence_source_chunk_hash"] == raw_hash
    assert snapshot["chunk_hash"] == span_hash
    assert snapshot["indexed_chunk_hash"] != snapshot["chunk_hash"]
    assert snapshot["evidence_scope_as_of"].startswith("2025-06-01T00:00:00")
    assert snapshot["evidence_scope_snapshot_origin"] == "user_literal"
    assert snapshot["originating_assistant_message_id"] == str(origin)
    assert snapshot["coverage_origin_message_id"] == str(origin)
    assert snapshot["coverage_partial"] is True
    assert snapshot["config_provenance"]["project_config_revision_number"] == 9


def test_new_review_snapshots_omit_coverage_origin() -> None:
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Refunds are due within 30 days.",
        score=0.9,
        filename="policy.txt",
        chunk_hash="hash",
    )
    origin = uuid.uuid4()
    snapshot = build_citation_snapshots(
        [chunk],
        project_id=uuid.uuid4(),
        config_snapshot_id=uuid.uuid4(),
        config_provenance={},
        prompt_version="v1",
        config=ChatConfig(),
        originating_assistant_message_id=origin,
        coverage_origin_message_id=None,
    )[0]
    assert snapshot["originating_assistant_message_id"] == str(origin)
    assert snapshot["coverage_origin_message_id"] is None


def test_legacy_snapshot_without_provenance_version_remains_readable() -> None:
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="legacy",
        score=0.5,
        filename="old.txt",
        chunk_hash="abc",
    )
    snapshot = CitationSnapshot.model_validate(
        {
            "source_kind": "knowledge",
            "chunk_id": str(chunk.chunk_id),
            "project_id": str(uuid.uuid4()),
            "document_id": str(chunk.document_id),
            "filename": chunk.filename,
            "chunk_index": 0,
        }
    )
    assert snapshot.evidence_provenance_version is None
    assert snapshot.indexed_chunk_hash is None


def test_reconstructed_table_context_is_labeled_not_a_contiguous_slice() -> None:
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Tax | Rate",
        score=0.8,
        filename="schedule.txt",
        chunk_hash="table",
        metadata={"table_context": "Schedule 1 heading"},
    )
    snapshot = build_citation_snapshots(
        [chunk],
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v5",
        config=ChatConfig(),
    )[0]
    assert snapshot["evidence_source_envelope"] == "reconstructed_context"
