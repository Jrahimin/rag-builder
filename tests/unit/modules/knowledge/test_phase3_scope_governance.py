"""Offline source truth, conservative eligibility and source-bound decision contracts."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.knowledge.scope_facts import (
    ScopeFact,
    affirmatively_incompatible,
    extract_scope_facts,
    span_hash,
)
from app.modules.knowledge.source_reconciliation import ReconciliationProof, decide_reconciliation

pytestmark = pytest.mark.unit
CAPTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures/evaluation/phase2_captured_budget_speech_v1.json"
)


def _span(text, role="text"):
    return {
        "text": text,
        "role": role,
        "char_start": 0,
        "char_end": len(text),
        "provenance": "exact_source_span",
    }


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Assessment years 2026-27, 2027-28 and 2028-29", "assessment"),
        ("Fiscal years 2026-27 and 2028-29", "fiscal"),
        (
            "করবর্ষ \u09e8\u09e6\u09e8\u09ec-\u09e8\u09ed, "
            "\u09e8\u09e6\u09e8\u09ed-\u09e8\u09ee এবং "
            "\u09e8\u09e6\u09e8\u09ee-\u09e8\u09ef",
            "assessment",
        ),
    ],
)
def test_plural_periods_are_typed_local_mentions(text, kind):
    facts = extract_scope_facts([_span(text, "table")], unit_id="table-1")
    periods = [f for f in facts if f["kind"] == "period"]
    assert len(periods) == (3 if kind == "assessment" else 2)
    assert all(f["version"] == "scope.v2" and f["legal_kind"] == kind for f in periods)
    assert all(f["scope"] == "mention" and f["locality"] == "table" for f in periods)
    assert not affirmatively_incompatible(
        periods, [{"kind": kind, "start_year": 2025, "end_year": 2026}]
    )


def _governing(**changes):
    span = _span("This instrument applies exclusively for Assessment Year 2026-27.")
    data = {
        "kind": "period",
        "value": "2026-27",
        "legal_kind": "assessment",
        "start_year": 2026,
        "end_year": 2027,
        "scope": "governing",
        "locality": "document",
        "locality_id": "document-1",
        "effect": "operative",
        "exhaustive": True,
        "source_span": span,
        "status": "reviewed",
        "review_provenance": {
            "reviewer": "source-review-fixture",
            "evidence_hash": span_hash(span["text"]),
            "reason": "explicit exclusive document scope",
        },
    }
    data.update(changes)
    return ScopeFact(**data).model_dump(mode="json")


@pytest.mark.parametrize(
    "facts",
    [
        [],
        [{"kind": "period", "legal_kind": "assessment", "start_year": 2026, "end_year": 2027}],
        [_governing(locality="table")],
        [_governing(locality="provision")],
        [_governing(effect="proposal")],
        [_governing(effect="example")],
        [_governing(exhaustive=False)],
    ],
)
def test_unknown_legacy_local_or_nonoperative_scopes_remain_eligible(facts):
    assert not affirmatively_incompatible(
        facts, [{"kind": "assessment", "start_year": 2025, "end_year": 2026}]
    )


def test_only_reviewed_exhaustive_document_scope_can_exclude():
    facts = [_governing()]
    assert affirmatively_incompatible(
        facts, [{"kind": "assessment", "start_year": 2025, "end_year": 2026}]
    )
    assert not affirmatively_incompatible(
        facts, [{"kind": "assessment", "start_year": 2026, "end_year": 2027}]
    )
    assert not affirmatively_incompatible(
        facts, [{"kind": "fiscal", "start_year": 2025, "end_year": 2026}]
    )
    assert not affirmatively_incompatible(
        facts,
        [
            {"kind": "assessment", "start_year": 2025, "end_year": 2026},
            {"kind": "fiscal", "start_year": 2025, "end_year": 2026},
        ],
    )
    with pytest.raises(ValueError):
        _governing(review_provenance={})
    with pytest.raises(ValueError):
        _governing(
            review_provenance={
                "reviewer": "fixture",
                "evidence_hash": "b" * 64,
                "reason": "mismatched quote",
            }
        )


def test_actual_captured_proposal_periods_never_establish_legal_effect():
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    text = "\n".join(row["content"] for row in capture["rows"] if row["chunk_index"] in {180, 181})
    facts = extract_scope_facts([_span(text, "table")], unit_id="captured-180-181")
    assert {(f["start_year"], f["end_year"]) for f in facts} >= {
        (2026, 2027),
        (2027, 2028),
        (2030, 2031),
    }
    assert all(f["effect"] == "proposal" and f["scope"] == "mention" for f in facts)
    assert not affirmatively_incompatible(
        facts, [{"kind": "assessment", "start_year": 2025, "end_year": 2026}]
    )
    assert capture["provenance"]["source"]["effective_from"] is None


@pytest.mark.parametrize(
    "changed,missing,proposed,expected",
    [
        (False, (), "same", "re_attest"),
        (True, (), "same", "private_reprocess"),
        (False, (), "changed", "private_reprocess"),
        (False, ("missing_schedule",), "same", "reacquire_reparse"),
        (False, (), None, "unresolved"),
    ],
)
def test_saved_source_decisions_keep_identity_spans_and_unresolved_obligations(
    changed, missing, proposed, expected
):
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    source = capture["provenance"]["source"]
    row = next(row for row in capture["rows"] if row["chunk_index"] == 181)
    proof = ReconciliationProof(
        project_id=row["project_id"],
        document_id=source["document_id"],
        source_revision_id=source["source_revision_id"],
        index_build_id=capture["provenance"]["build"]["id"],
        chunk_id=row["id"],
        source_content_hash=source["content_sha256"],
        chunk_hash=span_hash(row["content"]),
        quote=row["content"],
        char_start=0,
        char_end=len(row["content"]),
        reviewer="captured-proof-fixture",
        reviewed_at=datetime.now(UTC),
        reason="Attested SELECT export; proposal only",
        effect="proposal",
    )
    proposed_hash = (
        source["content_sha256"] if proposed == "same" else "a" * 64 if proposed else None
    )
    decision = decide_reconciliation(
        proof,
        chunk_content=row["content"],
        current_source_hash=source["content_sha256"],
        proposed_source_hash=proposed_hash,
        structure_changed=changed,
        missing_components=missing,
    )
    assert decision.action == expected and decision.execution_status == "pending"
    assert decision.proof == proof and "governing_period_unestablished" in decision.obligations
    assert decision.proof.target_revision_id is None and decision.proof.effective_period is None
    with pytest.raises(ValueError):
        decide_reconciliation(
            proof,
            chunk_content=row["content"] + " changed",
            current_source_hash=source["content_sha256"],
            proposed_source_hash=proposed_hash,
            structure_changed=changed,
        )


def test_relationship_unknown_effect_never_creates_a_replacement_scope():
    text = "Section 109 is amended subject to the separately published footnote."
    proof = ReconciliationProof(
        project_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        source_revision_id=uuid.uuid4(),
        index_build_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        source_content_hash=span_hash(text),
        chunk_hash=span_hash(text),
        quote=text,
        char_start=0,
        char_end=len(text),
        reviewer="fixture",
        reviewed_at=datetime.now(UTC),
        reason="footnote missing",
        relationship="modifies",
    )
    decision = decide_reconciliation(
        proof,
        chunk_content=text,
        current_source_hash=span_hash(text),
        proposed_source_hash=span_hash(text),
        structure_changed=False,
        missing_components=("missing_footnote_chain",),
    )
    assert "review_exact_relationship_effect_and_target_scope" in decision.obligations
    assert decision.action == "reacquire_reparse" and proof.effect == "unknown"
