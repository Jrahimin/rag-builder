"""Unit tests for citation snapshot builder."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.ports import ContextChunk

pytestmark = pytest.mark.unit


def test_build_citation_snapshots_includes_hash_and_excerpt() -> None:
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="x" * 300,
        score=0.9,
        filename="doc.txt",
        chunk_hash="deadbeef",
    )
    snapshots = build_citation_snapshots(
        [chunk],
        config=ChatConfig(citation_excerpt_max_chars=50),
    )
    assert len(snapshots) == 1
    assert snapshots[0]["chunk_hash"] == "deadbeef"
    assert len(snapshots[0]["excerpt"]) == 50
