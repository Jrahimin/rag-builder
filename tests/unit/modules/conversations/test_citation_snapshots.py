"""Unit tests for citation snapshot builder."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import CitationSnapshot, CitationSourceKind

pytestmark = pytest.mark.unit


def test_build_citation_snapshots_includes_hash_and_excerpt() -> None:
    project_id = uuid.uuid4()
    index_build_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    group_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
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
                {"relationship_type": "replaces", "target_revision_id": str(uuid.uuid4())}
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
