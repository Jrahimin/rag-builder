"""Structural neighbour ranking is independent of UUID order and synthetic pages."""

from __future__ import annotations

import uuid

import pytest

from app.modules.retrieval.adjacent_selection import (
    AdjacentChunkRef,
    page_provenance_is_meaningful,
    select_adjacent_ids,
    structural_relation,
)

pytestmark = pytest.mark.unit


def _chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    version: int = 1,
    index: int,
    page: int | None = 1,
    page_end: int | None = None,
    metadata: dict | None = None,
) -> AdjacentChunkRef:
    return AdjacentChunkRef(
        id=chunk_id or uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        document_version=version,
        chunk_index=index,
        page_start=page,
        page_end=page_end if page_end is not None else page,
        page_number=page,
        metadata=metadata or {},
    )


def test_markdown_synthetic_page_returns_index_neighbour_before_unrelated_sections():
    document = uuid.uuid4()
    markdown = {
        "strategy_used": "markdown",
        "heading_path": ["Section 36"],
        "section_title": "Section 36",
        "structure_signals": {"source_format": "md", "markdown_detected": True},
    }
    other = {
        "strategy_used": "markdown",
        "heading_path": ["Unrelated"],
        "section_title": "Unrelated",
        "structure_signals": {"source_format": "md", "markdown_detected": True},
    }
    predecessor_id = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    anchor_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    unrelated = [
        _chunk(
            chunk_id=uuid.UUID(int=index),
            document_id=document,
            index=index,
            metadata=other,
        )
        for index in range(5, 53)
        if index not in {48, 49, 50}
    ]
    predecessor = _chunk(
        chunk_id=predecessor_id,
        document_id=document,
        index=48,
        metadata=markdown,
    )
    continuation = _chunk(
        chunk_id=uuid.uuid4(),
        document_id=document,
        index=50,
        metadata=markdown,
    )
    anchor = _chunk(chunk_id=anchor_id, document_id=document, index=49, metadata=markdown)
    selected = select_adjacent_ids(
        anchors=[anchor], candidates=[*unrelated, continuation, predecessor]
    )
    assert predecessor_id in selected
    assert continuation.id in selected
    assert {predecessor_id, continuation.id} == set(selected[:2])
    assert unrelated[0].id not in selected[:2]
    assert len(selected) <= 48


def test_multiple_anchors_receive_a_fair_share_of_the_neighbour_budget():
    first_doc, second_doc = uuid.uuid4(), uuid.uuid4()

    def heading(name: str) -> dict:
        return {
            "strategy_used": "markdown",
            "heading_path": [name],
            "section_title": name,
            "structure_signals": {"source_format": "md"},
        }

    first_anchor = _chunk(document_id=first_doc, index=10, metadata=heading("A"))
    second_anchor = _chunk(document_id=second_doc, index=20, metadata=heading("B"))
    first_neighbours = [
        _chunk(document_id=first_doc, index=9, metadata=heading("A")),
        _chunk(document_id=first_doc, index=11, metadata=heading("A")),
    ]
    second_neighbours = [
        _chunk(document_id=second_doc, index=19, metadata=heading("B")),
        _chunk(document_id=second_doc, index=21, metadata=heading("B")),
    ]
    selected = select_adjacent_ids(
        anchors=[first_anchor, second_anchor],
        candidates=[*first_neighbours, *second_neighbours],
        limit=4,
    )
    assert {chunk.id for chunk in first_neighbours}.issubset(selected)
    assert {chunk.id for chunk in second_neighbours}.issubset(selected)


def test_pdf_table_page_neighbours_remain_discoverable_with_nonconsecutive_indices():
    document = uuid.uuid4()
    table = {
        "strategy_used": "structure",
        "element_type": "table",
        "structure_signals": {"source_format": "pdf", "markdown_detected": False},
    }
    anchor = _chunk(document_id=document, index=10, page=5, metadata=table)
    previous_page = _chunk(document_id=document, index=3, page=4, metadata=table)
    next_page = _chunk(document_id=document, index=18, page=6, metadata=table)
    elsewhere = _chunk(document_id=document, index=40, page=12, metadata=table)
    selected = select_adjacent_ids(
        anchors=[anchor],
        candidates=[elsewhere, next_page, previous_page],
    )
    assert previous_page.id in selected
    assert next_page.id in selected
    assert elsewhere.id not in selected
    assert page_provenance_is_meaningful(anchor) is True


def test_pdf_table_page_range_includes_page_after_anchor_end():
    document = uuid.uuid4()
    table = {
        "strategy_used": "structure",
        "element_type": "table",
        "structure_signals": {"source_format": "pdf"},
    }
    anchor = _chunk(document_id=document, index=10, page=5, page_end=6, metadata=table)
    following = _chunk(document_id=document, index=30, page=7, metadata=table)
    selected = select_adjacent_ids(anchors=[anchor], candidates=[following])
    assert selected == (following.id,)


def test_other_document_version_and_document_cannot_be_selected():
    document = uuid.uuid4()
    markdown = {"strategy_used": "markdown", "heading_path": ["S"], "section_title": "S"}
    anchor = _chunk(document_id=document, version=2, index=5, metadata=markdown)
    other_version = _chunk(document_id=document, version=1, index=4, metadata=markdown)
    other_document = _chunk(index=4, metadata=markdown)
    selected = select_adjacent_ids(
        anchors=[anchor],
        candidates=[other_version, other_document],
    )
    assert selected == ()
    assert structural_relation(anchor, other_version) is None


def test_empty_anchor_list_returns_no_neighbours():
    assert select_adjacent_ids(anchors=[], candidates=[_chunk(index=1)]) == ()


def test_same_section_continuation_beyond_eight_chunks_is_still_ranked():
    document = uuid.uuid4()
    heading = {
        "strategy_used": "markdown",
        "heading_path": ["Section 36"],
        "section_title": "Section 36",
    }
    anchor = _chunk(document_id=document, index=49, metadata=heading)
    distant = _chunk(document_id=document, index=40, metadata=heading)
    selected = select_adjacent_ids(anchors=[anchor], candidates=[distant])
    assert distant.id in selected


def test_headingless_markdown_keeps_near_index_neighbours():
    document = uuid.uuid4()
    markdown = {"strategy_used": "markdown", "structure_signals": {"source_format": "md"}}
    anchor = _chunk(document_id=document, index=10, metadata=markdown)
    near = _chunk(document_id=document, index=12, metadata=markdown)
    far = _chunk(document_id=document, index=40, metadata=markdown)
    selected = select_adjacent_ids(anchors=[anchor], candidates=[near, far])
    assert near.id in selected
    assert far.id not in selected
