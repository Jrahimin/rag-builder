"""Focused tests for final retrieval duplicate suppression."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import RetrievalConfig
from app.modules.retrieval.schemas.search import RetrievalResult
from app.modules.retrieval.services.duplicate_suppression_service import (
    DuplicateSuppressionService,
)

pytestmark = pytest.mark.unit


def _result(
    *,
    document_id: uuid.UUID,
    chunk_index: int,
    content: str,
    section: str | None = None,
) -> RetrievalResult:
    metadata = {"section_title": section} if section else {}
    return RetrievalResult(
        chunk_id=uuid.uuid4(),
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        score=1.0 - chunk_index / 100,
        filename="policy.txt",
        metadata=metadata,
    )


def test_suppression_preserves_ranked_order_and_removes_exact_content() -> None:
    first_doc = uuid.uuid4()
    second_doc = uuid.uuid4()
    ranked = [
        _result(document_id=first_doc, chunk_index=0, content="Refunds are thirty days."),
        _result(document_id=first_doc, chunk_index=1, content="Refunds are thirty days."),
        _result(document_id=second_doc, chunk_index=0, content="Escalate to support."),
    ]

    selected = DuplicateSuppressionService(RetrievalConfig()).select(ranked, limit=3)

    assert [item.chunk_id for item in selected.results] == [ranked[0].chunk_id, ranked[2].chunk_id]
    assert selected.suppressed_by_reason == {"content_hash": 1}


def test_suppression_limits_documents_and_sections_without_mutating_metadata() -> None:
    document_id = uuid.uuid4()
    other_document = uuid.uuid4()
    ranked = [
        _result(document_id=document_id, chunk_index=0, content="one", section="Refunds"),
        _result(document_id=document_id, chunk_index=1, content="two", section="Refunds"),
        _result(document_id=document_id, chunk_index=2, content="three", section="Refunds"),
        _result(document_id=document_id, chunk_index=3, content="four", section="Security"),
        _result(document_id=document_id, chunk_index=4, content="five", section="Privacy"),
        _result(document_id=other_document, chunk_index=0, content="six", section="Other"),
    ]
    config = RetrievalConfig(max_chunks_per_document=3, max_chunks_per_section=2)

    selected = DuplicateSuppressionService(config).select(ranked, limit=6)

    assert [item.content for item in selected.results] == ["one", "two", "four", "six"]
    assert selected.suppressed_by_reason == {"document_limit": 1, "section_limit": 1}
    assert selected.results[0].metadata == {"section_title": "Refunds"}
