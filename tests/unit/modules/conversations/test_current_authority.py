"""Provision-scoped current-authority admission tests."""

from __future__ import annotations

import uuid

import pytest

from app.modules.conversations.current_authority import remove_superseded_provisions
from app.modules.conversations.ports import ContextChunk

pytestmark = pytest.mark.unit


def _chunk(*, revision: uuid.UUID, content: str, records: list[dict] | None = None) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="source.md",
        chunk_hash="hash",
        metadata={
            "source_revision_id": str(revision),
            "modifies_expansion_records": records or [],
        },
    )


def test_exact_modified_provision_is_removed_but_neighboring_rules_remain() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content=(
            "Section 20 — Eligible Investment\nApproved savings certificates.\n\n"
            "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
            "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
        ),
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")

    safe = remove_superseded_provisions([base_chunk, modifier_chunk])

    assert "Approved savings certificates" in safe[0].content
    assert "15%" not in safe[0].content
    assert "cannot exceed tax liability" in safe[0].content
    assert safe[0].metadata["authority_redacted_provisions"] == [
        "Section 21 — Investment Rebate Rate"
    ]


def test_unscoped_or_unresolved_relationship_never_suppresses_whole_document() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "target_provisions": [],
        }
    ]
    base_chunk = _chunk(revision=base, records=records, content="Section 20\nStill valid.")
    modifier_chunk = _chunk(revision=modifier, records=records, content="Amendment.")
    safe = remove_superseded_provisions([base_chunk, modifier_chunk])
    assert safe[0].content == base_chunk.content


def test_scope_is_not_applied_when_modifier_is_absent_from_recall() -> None:
    base = uuid.uuid4()
    records = [
        {
            "outcome": "expanded",
            "base_revision_id": str(base),
            "modifier_revision_id": str(uuid.uuid4()),
            "target_provisions": ["Section 21"],
        }
    ]
    base_chunk = _chunk(revision=base, records=records, content="Section 21\nHistorical text.")
    assert remove_superseded_provisions([base_chunk])[0].content == base_chunk.content
