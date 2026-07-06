"""Unit tests for ContextBuilder."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.ports import ContextChunk
from app.platform.domain.content_hash import content_hash

pytestmark = pytest.mark.unit


def _chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    content: str = "hello",
    score: float = 1.0,
) -> ContextChunk:
    cid = chunk_id or uuid.uuid4()
    return ContextChunk(
        chunk_id=cid,
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=score,
        filename="doc.txt",
        chunk_hash=content_hash(content),
    )


def test_deduplicates_by_chunk_id() -> None:
    chunk_id = uuid.uuid4()
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select(
        [_chunk(chunk_id=chunk_id), _chunk(chunk_id=chunk_id, content="dup")]
    )
    assert len(selected) == 1


def test_preserves_input_order() -> None:
    first = _chunk(content="first", score=0.9)
    second = _chunk(content="second", score=0.5)
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select([first, second])
    assert [chunk.content for chunk in selected] == ["first", "second"]


def test_enforces_max_context_chunks() -> None:
    chunks = [_chunk(content=f"c{i}") for i in range(5)]
    builder = ContextBuilder(ChatConfig(max_context_chunks=2, context_char_budget=10_000))
    selected = builder.select(chunks)
    assert len(selected) == 2


def test_enforces_char_budget() -> None:
    chunks = [_chunk(content="a" * 600)]
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=500))
    selected = builder.select(chunks)
    assert len(selected) == 1
    assert len(selected[0].content) == 500


def test_deduplicates_by_chunk_hash() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    shared_hash = content_hash("duplicate-content")
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select(
        [
            ContextChunk(
                chunk_id=first_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="first",
                score=0.9,
                filename="doc.txt",
                chunk_hash=shared_hash,
            ),
            ContextChunk(
                chunk_id=second_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="second",
                score=0.8,
                filename="doc.txt",
                chunk_hash=shared_hash,
            ),
        ]
    )
    assert len(selected) == 1
