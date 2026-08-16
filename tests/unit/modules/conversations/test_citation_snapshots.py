"""Unit tests for citation snapshot builder."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.ports import ContextChunk

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
