"""Unit tests for ChunkingService."""

from __future__ import annotations

import pytest

from app.modules.knowledge.services.chunking_service import ChunkingService

pytestmark = pytest.mark.unit


def test_split_produces_multiple_chunks_with_overlap() -> None:
    text = "word " * 300
    service = ChunkingService(chunk_size=100, chunk_overlap=20)
    chunks = service.split(text.strip(), page_count=1)

    assert len(chunks) > 1
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert chunks[0].page_number == 1
    assert chunks[0].token_count > 0
    assert chunks[0].char_start < chunks[0].char_end


def test_split_empty_text_returns_no_chunks() -> None:
    service = ChunkingService(chunk_size=500, chunk_overlap=50)
    assert service.split("   ") == []


def test_smaller_chunk_size_increases_chunk_count() -> None:
    text = "paragraph " * 200
    large = ChunkingService(chunk_size=1000, chunk_overlap=100)
    small = ChunkingService(chunk_size=200, chunk_overlap=20)
    assert len(small.split(text)) > len(large.split(text))
