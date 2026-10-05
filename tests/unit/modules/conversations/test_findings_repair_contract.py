"""Offline publication regressions motivated by the frozen October QA captures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import ChatConfig
from app.core.exception_handlers import register_exception_handlers
from app.modules.conversations.answer_draft import render_verified_segments
from app.modules.conversations.grounding_service import (
    GroundingResult,
    GroundingService,
    _evidence_snapshot,
    _SelectedSpan,
)
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.message_execution_runner_service import (
    _format_user_facing_gap_details,
    _has_incomplete_trailing_fragment,
    _published_citations,
    _user_facing_gap_details,
)
from app.platform.domain.publication_integrity import complete_publication_unit, damaged_source_text
from tests.unit.modules.conversations.test_phase2_completion import execute_contract, source
from tests.unit.modules.conversations.test_post_qa_contract import captured_source, journey

pytestmark = pytest.mark.unit


def test_unhandled_response_correlates_header_and_body():
    app = FastAPI()
    register_exception_handlers(app)

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.request_id = "frozen-failure-correlation"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/fixture")
    async def fail():
        raise RuntimeError("private fixture detail")

    response = TestClient(app, raise_server_exceptions=False).get("/fixture")
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert "private fixture detail" not in response.text


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
async def test_supported_sibling_survives_unavailable_verifier_without_rejected_text(
    transport, monkeypatch
):
    service, provider, conversation, repository, question = journey()
    original_map = GroundingService.map_claims

    async def reject_sibling(self, content, chunks, **kwargs):
        result = await original_map(self, content, chunks, **kwargs)
        claims = [
            {
                **claim,
                "verification": "unverified",
                "grounded": False,
                "verification_reason": "verifier_unavailable",
            }
            if "R2" in claim.get("requirement_ids", [])
            else claim
            for claim in result.claims
        ]
        return GroundingResult(claims=claims, grounded=False, citation_coverage=1.0)

    monkeypatch.setattr(GroundingService, "map_claims", reject_sibling)
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["terminal_outcome"]["outcome"] == "partial"
    assert answer["terminal_outcome"]["supported_requirement_ids"] == ["R1"]
    assert answer["terminal_outcome"]["unresolved_requirement_ids"] == ["R2"]
    assert "18 months" in answer["content"]
    assert "15 months" not in answer["content"]
    assert all(claim["verification"] == "supported" for claim in answer["claims"])


@pytest.mark.parametrize(
    "text",
    [
        "The proposal is gradually increased in subsequent [1]",
        "The limit is increased in subsequent y [2]",
        "The applicant must provide a certificate and [3]",
    ],
)
def test_rendered_citation_cannot_hide_cutoff(text):
    assert _has_incomplete_trailing_fragment(text)


def test_complete_coverage_rejects_damaged_procedure_but_retains_readable_partial():
    evidence = source("An applicant must supply identification.\n" + "\ufffd" * 80)
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "fulfillment": "full",
                    "answerable_scope": "An applicant must supply identification.",
                    "evidence": [
                        {
                            "chunk_id": str(evidence.chunk_id),
                            "quote": "An applicant must supply identification.",
                        }
                    ],
                }
            ],
        }
    )
    assert not verdict.validates([], [evidence], {"R1"})
    verdict.complete = False
    verdict.missing = ["The remaining procedure is unreadable."]
    verdict.checks[0].fulfillment = "partial"
    verdict.retain_answerable_scopes()
    assert verdict.partial_validates([evidence], {"R1"})


def test_captured_unflagged_damaged_procedure_cannot_certify_complete_checklist():
    fixture = json.loads(
        (
            Path(__file__).parents[3] / "fixtures/evaluation/captured_damaged_procedure_v1.json"
        ).read_text(encoding="utf-8")
    )
    quote = fixture["quote"]
    assert hashlib.sha256(quote.encode()).hexdigest() == fixture["quote_sha256"]
    evidence = source(quote)
    assert evidence.metadata == {}
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "fulfillment": "full",
                    "evidence": [{"chunk_id": str(evidence.chunk_id), "quote": quote}],
                }
            ],
        }
    )
    assert not verdict.validates([], [evidence], {"R1"})


def test_citation_projection_remaps_only_published_spans_and_preserves_replay_identity():
    citations = [
        {"chunk_id": "unused", "excerpt": "unrelated"},
        {
            "chunk_id": "used",
            "excerpt": "Whole source",
            "char_start": 100,
            "char_end": 900,
            "page_number": 5,
            "evidence_span_hash": "original-hash",
            "supporting_spans": [
                {"requirement_id": "R1", "quote": "Direct proof"},
                {"requirement_id": "R2", "quote": "Unpublished sibling"},
            ],
        },
    ]
    claims = [
        {
            "text": "Direct proof [2]",
            "verification": "supported",
            "requirement_ids": ["R1"],
            "evidence": [
                {
                    "citation_index": 2,
                    "excerpt": "Direct proof",
                    "char_start": 140,
                    "char_end": 152,
                    "page_number": 5,
                }
            ],
        }
    ]
    content, result = _published_citations("Direct proof [2]", citations, claims)
    assert content == "Direct proof [1]"
    assert len(result) == 1
    assert result[0]["excerpt"] == "Direct proof"
    assert result[0]["char_start"] == 140 and result[0]["char_end"] == 152
    assert result[0]["supporting_spans"] == [{"requirement_id": "R1", "quote": "Direct proof"}]
    assert result[0]["evidence_span_hash"] == "original-hash"
    assert claims[0]["evidence"][0]["citation_index"] == 1
    assert claims[0]["text"] == content
    # Projection must not rewrite older persisted citation snapshots in place.
    assert citations[0] == {"chunk_id": "unused", "excerpt": "unrelated"}
    assert citations[1]["excerpt"] == "Whole source"
    assert citations[1]["char_start"] == 100 and citations[1]["char_end"] == 900
    assert citations[1]["supporting_spans"] == [
        {"requirement_id": "R1", "quote": "Direct proof"},
        {"requirement_id": "R2", "quote": "Unpublished sibling"},
    ]


def test_duplicate_verified_quote_is_rendered_once():
    segments = [
        {"assertion_id": identity, "text": "A supported provision.", "proof_ids": ["proof"]}
        for identity in ["A1", "A2"]
    ]
    assert (
        render_verified_segments(segments, supported_ids={"A1", "A2"}, proof_indexes={"proof": 1})
        == "A supported provision. [1]"
    )


def test_real_pipe_schedule_is_not_prose_corruption():
    evidence = captured_source("5aa3a215-d3da-4242-b16f-74df447729c4")
    assert "|" in evidence.content
    assert not damaged_source_text(evidence.content)
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "fulfillment": "full",
                    "evidence": [{"chunk_id": str(evidence.chunk_id), "quote": evidence.content}],
                }
            ],
        }
    )
    assert verdict.validates([], [evidence], {"R1"})


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize(
    "fragment",
    [
        "The applicant must submit an identifi [1]",
        "The proposed limit is gradually increased in subsequent [1]",
        "The applicant must submit an identifi [1]\n\nA later sentence is complete. [1]",
        "আবেদনকারীকে পরিচয়পত্র জমা দি [1]",
        "আবেদনকারীকে পরিচয়\ufffdপত্র জমা দিতে হবে। [1]",
    ],
)
async def test_incomplete_assertion_does_not_hide_before_complete_sibling(
    transport, position, fragment, monkeypatch
):
    service, provider, conversation, repository, question = journey()
    original_map = GroundingService.map_claims

    async def integrity_candidate(self, content, chunks, **kwargs):
        result = await original_map(self, content, chunks, **kwargs)
        # Replay a source-supported verifier result with an incomplete unit;
        # finalization must independently reject that unit before publication.
        result.claims[position]["text"] = fragment
        return result

    monkeypatch.setattr(GroundingService, "map_claims", integrity_candidate)
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["terminal_outcome"]["outcome"] == "partial"
    assert answer["terminal_outcome"]["coverage"] == "partial"
    assert fragment.replace(" [1]", "") not in answer["content"]
    assert len(answer["claims"]) == 1
    assert answer["claims"][0]["requirement_ids"] == (["R2"] if position == 0 else ["R1"])
    assert ("15 months" if position == 0 else "18 months") in answer["content"]


def test_captured_reconstructed_budget_proof_has_unknown_locator():
    fixture = json.loads(
        (
            Path(__file__).parents[3] / "fixtures/evaluation/captured_budget_publication_v1.json"
        ).read_text(encoding="utf-8")
    )
    citation = fixture["citation"]
    assert citation["page_number"] == 5 and citation["char_start"] is None
    quote = citation["supporting_spans"][0]["quote"]
    chunk = replace(
        source(quote),
        page_number=5,
        metadata={
            "provenance_precision": citation["provenance_precision"],
            "evidence_source_envelope": citation["evidence_source_envelope"],
        },
    )
    evidence = _evidence_snapshot(
        1, chunk, ChatConfig(), _SelectedSpan(quote, 0, len(quote), "reviewed_proof", None, False)
    ).model_dump(mode="json")
    assert evidence["page_number"] is None
    assert evidence["char_start"] is None and evidence["char_end"] is None
    claims = [
        {"text": "Verified proposal. [1]", "verification": "supported", "evidence": [evidence]}
    ]
    _, projected = _published_citations(claims[0]["text"], [citation], claims)
    assert projected[0]["page_number"] is None
    assert projected[0]["provenance_precision"] == "unknown_source_locator"
    assert projected[0]["evidence_span_hash"] == citation["evidence_span_hash"]


def test_distinct_attested_proof_spans_keep_distinct_source_locations():
    first, second = "A first complete rule.", "A second complete rule."
    chunk = replace(
        source(first + "\n" + second),
        page_number=5,
        metadata={
            "provenance_precision": "chunk_with_source_spans",
            "source_spans": [
                {
                    "text": text,
                    "char_start": start,
                    "char_end": start + len(text),
                    "page_start": page,
                    "page_end": page,
                    "provenance": "exact_source_span",
                }
                for text, start, page in [(first, 1000, 128), (second, 3000, 130)]
            ],
        },
    )
    evidences = [
        _evidence_snapshot(
            1, chunk, ChatConfig(), _SelectedSpan(text, 0, len(text), "reviewed_proof", None, False)
        ).model_dump(mode="json")
        for text in [first, second]
    ]
    assert [(row["page_number"], row["char_start"]) for row in evidences] == [
        (128, 1000),
        (130, 3000),
    ]
    claims = [{"text": "Two proven rules. [1]", "verification": "supported", "evidence": evidences}]
    _, projected = _published_citations(claims[0]["text"], [{"supporting_spans": []}], claims)
    assert projected[0]["page_number"] is None and projected[0]["char_start"] is None
    assert projected[0]["provenance_precision"] == "multiple_source_spans"
    assert [
        (row["page_number"], row["char_start"]) for row in projected[0]["supporting_spans"]
    ] == [(128, 1000), (130, 3000)]


def test_gap_notices_preserve_distinct_material_proof_gaps():
    assert _user_facing_gap_details(
        [
            "R1: The requested period is not established.",
            "R2: The requested period is not established.",
            "unproven governing dependency R1",
            "R3: The registration fee schedule is absent.",
            "R4: the requested period is not established",
            "unproven governing dependency R5.",
        ]
    ) == ["The requested period is not established.", "The registration fee schedule is absent."]


def test_gap_notices_join_every_description_without_double_punctuation():
    assert _format_user_facing_gap_details(
        [
            "R1: The requested period is not established.",
            "R2: The requested period is not established",
            "R3: The registration fee schedule is absent",
            "R4: The application checklist is unavailable!",
            "unproven governing dependency R5.",
        ]
    ) == (
        "The requested period is not established. The registration fee schedule is absent. "
        "The application checklist is unavailable!"
    )


def test_unpunctuated_prose_needs_explicit_completeness_and_negative_verdict_wins():
    assert not complete_publication_unit("The applicant must submit identification")
    assert complete_publication_unit(
        "The applicant must submit identification", semantic_complete=True
    )
    assert not complete_publication_unit(
        "An incomplete assertion looks complete.", semantic_complete=False
    )
    assert not complete_publication_unit(
        "আবেদনকারীকে পরিচয়\ufffdপত্র জমা দিতে হবে।", semantic_complete=True
    )
