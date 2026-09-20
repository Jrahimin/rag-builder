"""Provision-scoped current-authority admission tests."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.modules.conversations.current_authority import (
    annotate_authority_limitations,
    authority_record_affects_chunk,
    cited_authority_summary,
    remove_superseded_provisions,
)
from app.modules.conversations.ports import ContextChunk

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "content,unresolved",
    [
        ("Section 78 — Rebate\nCurrent rebate rule.", False),
        (
            "Income Tax Act 2023\n\nChapter 3 — Rebates\n\n"
            "Section 78 — Rebate\nCurrent rebate rule.",
            False,
        ),
        ("Section 106 — Administration\nAdministrative rule.", True),
        ("Section 106(1) — Administration\nAdministrative rule.", True),
        ("Continuation without a heading.", True),
        ("Unheaded continuation.\nSection 78 — Rebate\nRebate rule.", True),
        ("Section 78 — Rebate\nRule.\nSection 106 — Administration\nRule.", True),
    ],
)
def test_scoped_incomplete_amendment_only_blocks_potentially_affected_passages(content, unresolved):
    base = uuid.uuid4()
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content=content)],
        [
            {
                "base_revision_id": str(base),
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": ["Section 106", "Section 166"],
            }
        ],
    )
    assert (selected[0].metadata.get("authority_status") == "unresolved") is unresolved


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

    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)

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
    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)
    assert safe[0].content == base_chunk.content


def test_exact_bangla_modified_provision_is_removed_without_suppressing_neighbors() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    heading = "ধারা ২১ — বিনিয়োগ রিবেটের হার"
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "target_provisions": [heading],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content=(
            "ধারা ২০ — যোগ্য বিনিয়োগ\nঅনুমোদিত সঞ্চয়পত্র।\n\n"
            f"{heading}\nরিবেটের হার ১৫%।\n\n"
            "ধারা ২২ — রিবেটের সর্বোচ্চ সীমা\nরিবেট করের বেশি নয়।"
        ),
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")

    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)

    assert "অনুমোদিত সঞ্চয়পত্র" in safe[0].content
    assert "১৫%" not in safe[0].content
    assert "রিবেট করের বেশি নয়" in safe[0].content
    assert safe[0].metadata["authority_redacted_provisions"] == [heading]


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
    assert remove_superseded_provisions([base_chunk], records)[0].content == base_chunk.content


@pytest.mark.parametrize(
    "outcome",
    [
        "ungoverned_or_incomplete_metadata",
        "not_in_active_index",
        "source_cap_exceeded",
        "candidate_cap_exceeded",
    ],
)
def test_live_shaped_incomplete_edges_do_not_prove_current_authority(outcome: str) -> None:
    base = uuid.uuid4()
    chunk = _chunk(revision=base, content="78. General rebate: 15% or 1,000,000.")
    records = [
        {
            "relationship_type": "modifies",
            "outcome": outcome,
            "base_revision_id": str(base),
            "modifier_revision_id": str(uuid.uuid4()),
            "modifier_effective_from": None,
            "target_provisions": [],
        }
    ]
    selected = remove_superseded_provisions([chunk], records)
    assert selected[0].content == chunk.content
    assert selected[0].metadata["authority_status"] == "unresolved"
    assert selected[0].metadata["authority_limitations"][0]["reason"] == outcome
    assert "authority_status" not in chunk.metadata


@pytest.mark.parametrize("outcome", ["outside_as_of", "inactive", "stale_or_replaced_revision"])
def test_nonapplicable_amendment_does_not_taint_historical_evidence(outcome: str) -> None:
    base = uuid.uuid4()
    chunk = _chunk(revision=base, content="The contractual fee is 15%.")
    selected = remove_superseded_provisions(
        [chunk],
        [
            {
                "base_revision_id": str(base),
                "outcome": outcome,
                "target_provisions": [],
            }
        ],
    )
    assert "authority_status" not in selected[0].metadata


def test_modifier_recalled_then_budgeted_out_cannot_establish_authority() -> None:
    base = uuid.uuid4()
    chunk = _chunk(revision=base, content="78. Rebate: 15%.")
    selected = annotate_authority_limitations(
        [chunk],
        [
            {
                "base_revision_id": str(base),
                "modifier_revision_id": str(uuid.uuid4()),
                "outcome": "already_in_recall",
                "target_provisions": ["Section 78"],
            }
        ],
    )
    assert selected[0].metadata["authority_limitations"][0]["reason"] == (
        "modifier_absent_from_context"
    )


@pytest.mark.asyncio
async def test_exact_text_and_arithmetic_cannot_greenlight_unresolved_authority() -> None:
    from app.core.config import ChatConfig
    from app.modules.conversations.grounding_service import GroundingService

    base = uuid.uuid4()
    original = _chunk(revision=base, content="The investment rebate is 15%.")
    selected = remove_superseded_provisions(
        [original],
        [
            {
                "base_revision_id": str(base),
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
    )
    grounding = GroundingService(ChatConfig())
    answer = "The investment rebate is 15%. [1]\n\n60,000 × 15% = 9,000. [1]"  # noqa: RUF001
    before = await grounding.map_claims(answer, [original])
    after = await grounding.map_claims(answer, selected)
    assert before.grounded is True
    assert after.grounded is False
    assert all(claim["verification"] == "unverified" for claim in after.claims)
    assert all(claim["evidence_support"] == "supported" for claim in after.claims)
    assert all(claim["authority_status"] == "unresolved" for claim in after.claims)


def test_legacy_orphan_table_is_flagged_without_erasing_source_text() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(revision=uuid.uuid4(), content="Income | Rate\n450000 | 0%"),
        metadata={"element_type": "table"},
    )
    selected = remove_superseded_provisions([chunk], [])
    assert selected[0].content == chunk.content
    assert selected[0].metadata["authority_limitations"] == [
        {"reason": "table_applicability_context_missing"}
    ]


def test_unrelated_tax_expansion_does_not_demote_unassessed_company_citations() -> None:
    company = _chunk(revision=uuid.uuid4(), content="Section 36 — Annual list.")
    tax_base = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(tax_base),
            "modifier_revision_id": str(uuid.uuid4()),
            "outcome": "ungoverned_or_incomplete_metadata",
            "target_provisions": [],
        }
    ]
    selected = annotate_authority_limitations([company], records)
    assert selected[0].metadata.get("authority_status") != "unresolved"
    summary = cited_authority_summary(selected, records)
    assert summary["status"] == "not_assessed"
    assert summary["cited_record_count"] == 0
    assert summary["unresolved_citation_count"] == 0


def test_incoming_modifier_on_the_cited_base_is_assessed() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "outcome": "ungoverned_or_incomplete_metadata",
            "target_provisions": ["Section 36"],
        }
    ]
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content="Section 36 — Annual list.")],
        records,
    )
    assert selected[0].metadata["authority_status"] == "unresolved"
    summary = cited_authority_summary(selected, records)
    assert summary["status"] == "unresolved"
    assert summary["cited_record_count"] == 1


def test_missing_operative_scope_stays_visible_and_is_not_resolved() -> None:
    base = uuid.uuid4()
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content="Section 78 — Rebate.")],
        [
            {
                "base_revision_id": str(base),
                "modifier_revision_id": str(uuid.uuid4()),
                "outcome": "already_in_recall",
                "target_provisions": [],
            }
        ],
    )
    assert selected[0].metadata["authority_status"] == "unresolved"
    assert selected[0].metadata["authority_limitations"][0]["reason"] == "missing_provision_scope"


def test_disjoint_incomplete_record_on_cited_source_is_limited_not_resolved() -> None:
    base = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(base),
            "modifier_revision_id": str(uuid.uuid4()),
            "outcome": "ungoverned_or_incomplete_metadata",
            "target_provisions": ["Section 99"],
        }
    ]
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content="Section 36 — Annual list.")],
        records,
    )
    assert selected[0].metadata.get("authority_status") != "unresolved"
    summary = cited_authority_summary(selected, records)
    assert summary["status"] == "limited"
    assert summary["cited_record_count"] == 1


def test_disjoint_unavailable_modifier_is_limited_not_resolved() -> None:
    base = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(base),
            "modifier_revision_id": str(uuid.uuid4()),
            "outcome": "not_in_active_index",
            "target_provisions": ["Section 99"],
        }
    ]
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content="Section 36 — Annual list.")],
        records,
    )
    assert selected[0].metadata.get("authority_status") != "unresolved"
    assert cited_authority_summary(selected, records)["status"] == "limited"


def test_duplicate_modifier_without_scope_is_limited_not_resolved() -> None:
    base = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(base),
            "modifier_revision_id": str(uuid.uuid4()),
            "outcome": "duplicate",
            "target_provisions": [],
        }
    ]
    summary = cited_authority_summary(
        [_chunk(revision=base, content="Section 36 — Annual list.")], records
    )
    assert summary["status"] == "limited"


def test_newer_publication_does_not_replace_whole_document() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "modifier_published_date": "2026-07-01",
            "modifier_effective_from": "2026-07-01",
            "target_provisions": [],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content="Section 20 — Eligible Investment\nApproved savings certificates.",
    )
    modifier_chunk = _chunk(
        revision=modifier,
        records=records,
        content="Section 20 — Eligible Investment\nThe eligible list is replaced.",
    )
    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)
    assert "Approved savings certificates" in safe[0].content
    assert safe[0].metadata.get("authority_status") == "unresolved"


def test_older_modifier_publication_still_redacts_explicit_provision() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "modifier_published_date": "2020-01-01",
            "modifier_effective_from": "2020-01-01",
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
    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)
    assert "Approved savings certificates" in safe[0].content
    assert "15%" not in safe[0].content
    assert "cannot exceed tax liability" in safe[0].content


def test_uncommenced_modifier_does_not_redact_a_current_provision() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "modifier_effective_from": "2099-01-01",
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content=(
            "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
            "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
        ),
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")
    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records)
    assert "The rebate is 15%" in safe[0].content
    assert safe[0].metadata.get("authority_status") != "unresolved"


def test_future_as_of_still_redacts_a_commenced_modifier() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "modifier_effective_from": "2026-10-01",
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content=(
            "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
            "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
        ),
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")
    as_of = date(2027, 1, 1)
    safe = remove_superseded_provisions([base_chunk, modifier_chunk], records, reference_date=as_of)
    assert "15%" not in safe[0].content
    today_safe = remove_superseded_provisions(
        [base_chunk, modifier_chunk], records, reference_date=date(2026, 9, 1)
    )
    assert "The rebate is 15%" in today_safe[0].content


def test_dropping_required_modifier_cannot_make_base_provision_usable() -> None:
    base = uuid.uuid4()
    selected = annotate_authority_limitations(
        [_chunk(revision=base, content="Section 21 — Investment Rebate Rate\nThe rebate is 15%.")],
        [
            {
                "base_revision_id": str(base),
                "modifier_revision_id": str(uuid.uuid4()),
                "outcome": "already_in_recall",
                "modifier_effective_from": "2020-01-01",
                "target_provisions": ["Section 21 — Investment Rebate Rate"],
            }
        ],
    )
    assert selected[0].content.startswith("Section 21")
    assert selected[0].metadata["authority_status"] == "unresolved"
    assert selected[0].metadata["authority_limitations"][0]["reason"] == (
        "modifier_absent_from_context"
    )


def test_authority_record_affects_chunk_uses_identity_and_explicit_scope() -> None:
    base = uuid.uuid4()
    other = uuid.uuid4()
    matching = {
        "relationship_id": "edge-21",
        "base_revision_id": str(base),
        "modifier_revision_id": str(uuid.uuid4()),
        "target_provisions": ["Section 21"],
    }
    affected = _chunk(revision=base, content="Section 21 — Rebate\nThe rebate is 15%.")
    neighbor = _chunk(revision=base, content="Section 22 — Limit\nThe rebate cannot exceed tax.")
    unrelated = _chunk(revision=other, content="Section 21 — Rebate\nThe rebate is 15%.")
    assert authority_record_affects_chunk(matching, affected) is True
    assert authority_record_affects_chunk(matching, neighbor) is False
    assert authority_record_affects_chunk(matching, unrelated) is False
    unknown_scope = {**matching, "target_provisions": []}
    assert authority_record_affects_chunk(unknown_scope, affected) is True
    published_only = {
        "modifier_published_date": "2026-07-01",
        "target_provisions": ["Section 21"],
    }
    assert authority_record_affects_chunk(published_only, affected) is False


def test_modifier_chunk_with_its_own_heading_is_still_affected_by_its_record() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    record = {
        "relationship_id": "edge-21",
        "base_revision_id": str(base),
        "modifier_revision_id": str(modifier),
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
    }
    modifier_chunk = _chunk(
        revision=modifier,
        content=("Section 5 — Amendment of section 21\nIn section 21, for '15%' substitute '10%'."),
    )
    base_chunk = _chunk(
        revision=base,
        content="Section 21 — Investment Rebate Rate\nThe rebate is 15%.",
    )
    assert authority_record_affects_chunk(record, modifier_chunk) is True
    assert authority_record_affects_chunk(record, base_chunk) is True


def test_historical_annotation_clears_stale_unresolved_status() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "outcome": "already_in_recall",
            "modifier_effective_from": "2025-01-01",
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    chunk = _chunk(
        revision=base,
        records=records,
        content="Section 21 — Investment Rebate Rate\nThe rebate is 15%.",
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")
    today = annotate_authority_limitations(
        [chunk, modifier_chunk], records, reference_date=date(2026, 9, 1)
    )
    assert today[0].metadata.get("authority_status") == "unresolved"
    historical = remove_superseded_provisions(today, records, reference_date=date(2024, 6, 1))
    assert "The rebate is 15%" in historical[0].content
    assert historical[0].metadata.get("authority_status") != "unresolved"


def test_expired_modifier_is_not_applied_after_its_effective_interval() -> None:
    base = uuid.uuid4()
    modifier = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base),
            "modifier_revision_id": str(modifier),
            "modifier_effective_from": "2019-01-01",
            "modifier_effective_to": "2021-12-31",
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    base_chunk = _chunk(
        revision=base,
        records=records,
        content=(
            "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
            "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
        ),
    )
    modifier_chunk = _chunk(revision=modifier, records=records, content="The rebate is 10%.")
    current = remove_superseded_provisions(
        [base_chunk, modifier_chunk], records, reference_date=date(2024, 6, 1)
    )
    assert "The rebate is 15%" in current[0].content
    assert current[0].metadata.get("authority_status") != "unresolved"
    during = remove_superseded_provisions(
        [base_chunk, modifier_chunk], records, reference_date=date(2020, 6, 1)
    )
    assert "15%" not in during[0].content
