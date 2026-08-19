"""Unit tests for chunk validation."""

from __future__ import annotations

import pytest

from app.core.config import ChunkingConfig
from app.modules.knowledge.services.chunking.chunk_validation_service import ChunkValidationService
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService

pytestmark = pytest.mark.unit


def test_validation_removes_empty_chunks_without_reordering() -> None:
    validator = ChunkValidationService()
    drafts = [
        DraftChunk(content="First chunk with enough tokens to remain separate.", chunk_order=0),
        DraftChunk(content="   ", chunk_order=1),
        DraftChunk(content="Second chunk with enough tokens to remain separate.", chunk_order=2),
    ]
    validated = validator.validate(drafts, ChunkingConfig(min_tokens=5))
    assert [chunk.content for chunk in validated] == [
        "First chunk with enough tokens to remain separate.",
        "Second chunk with enough tokens to remain separate.",
    ]


def test_validation_splits_oversized_chunks_in_order() -> None:
    validator = ChunkValidationService()
    long_text = " ".join(f"word{i}" for i in range(500))
    drafts = [DraftChunk(content=long_text, chunk_order=0)]
    validated = validator.validate(drafts, ChunkingConfig(max_tokens=120))
    assert len(validated) > 1
    assert validated[0].chunk_order == 0
    assert validated[1].chunk_order == 1


def test_validation_merges_interior_identifier_fragment_forward() -> None:
    validator = ChunkValidationService()
    drafts = [
        DraftChunk(content="first section has enough words to remain independent", page_start=1),
        DraftChunk(content="\u09e6\u09ea।", page_start=2),
        DraftChunk(content="table heading and rows contain enough words to remain", page_start=2),
    ]

    validated = validator.validate(drafts, ChunkingConfig(min_tokens=5, max_tokens=100))

    assert len(validated) == 2
    assert validated[1].content.startswith("\u09e6\u09ea।")
    counter = TokenCountingService()
    assert all(chunk.token_count(counter) >= 5 for chunk in validated)


def test_validation_merges_leading_and_trailing_tiny_fragments() -> None:
    validator = ChunkValidationService()
    drafts = [
        DraftChunk(content="intro"),
        DraftChunk(content="middle content contains enough words for the configured minimum"),
        DraftChunk(content="tail"),
    ]

    validated = validator.validate(drafts, ChunkingConfig(min_tokens=5, max_tokens=100))

    assert len(validated) == 1
    assert validated[0].content.startswith("intro")
    assert validated[0].content.endswith("tail")
