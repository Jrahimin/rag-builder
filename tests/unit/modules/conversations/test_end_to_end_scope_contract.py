"""Paired correctness cases for the shared request and proof contracts."""

import uuid
from datetime import UTC, datetime

from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.services.chat_service import _render_structured_answer
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import (
    EvidenceRequirement,
    _guard_review_fulfillment,
)
from app.modules.conversations.turn_resolution import normalize_request_scope


def test_assessment_period_never_invents_timestamp():
    scope = normalize_request_scope(
        "Threshold for AY 2025-26 in this corpus", reference_time=datetime(2026, 9, 30, tzinfo=UTC)
    )
    assert scope.requested_periods[0].kind == "assessment"
    assert scope.requested_periods[0].start_year == 2025
    assert scope.exact_as_of is None
    assert scope.source_restriction == "indexed_only"
    current = normalize_request_scope("Current threshold")
    assert not current.requested_periods and current.exact_as_of is None


def test_known_at_scope_does_not_equate_to_applicable_rule():
    assert normalize_request_scope("Information known then").temporal_basis == "known_at"
    assert normalize_request_scope("Rule applicable then").temporal_basis == "applicable_rule"


def source(text):
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="source.txt",
        content=text,
        score=1,
        chunk_index=0,
        chunk_hash="test",
        metadata={
            "reviewed_proof": [
                {
                    "requirement_id": "R1",
                    "quote": text,
                    "fulfillment": "full",
                    "supported_scope": text,
                }
            ]
        },
    )


def test_structured_renderer_uses_proof_ids_and_rejects_foreign_ids():
    import json

    chunk = source("The rate is ten percent.")
    draft = {
        "segments": [
            {
                "text": "The rate is ten percent.",
                "requirement_ids": ["R1"],
                "proof_ids": [str(chunk.chunk_id)],
            }
        ]
    }
    result = _render_structured_answer(json.dumps(draft), [chunk])
    assert result[0] == "The rate is ten percent. [1]"
    draft["segments"][0]["proof_ids"] = [str(uuid.uuid4())]
    assert (
        _render_structured_answer(json.dumps(draft), [chunk])[1]["status"] == "failed_verification"
    )


def test_multilingual_condition_uses_facets_not_english_label():
    chunk = source("ভাড়াটিয়ার ক্ষেত্রে ভাড়ার চুক্তিপত্র। কারখানার ক্ষেত্রে অনুমতিপত্র।")
    requirement = EvidenceRequirement(
        requirement_id="R1", description="Conditional application checklist"
    )
    check = {
        "requirement_id": "R1",
        "description": requirement.description,
        "supported": True,
        "fulfillment": "full",
        "evidence": [{"chunk_id": str(chunk.chunk_id), "quote": chunk.content}],
        "condition_facets": [
            {
                "who": "tenant",
                "action": "supply lease",
                "condition": "if renting",
                "evidence_indexes": [0],
            },
            {
                "who": "factory",
                "action": "supply permit",
                "condition": "if operating factory",
                "evidence_indexes": [0],
            },
        ],
    }
    verdict = CoverageVerdict.model_validate({"complete": True, "missing": [], "checks": [check]})
    assert _guard_review_fulfillment(verdict, [requirement], [chunk]).complete
    check["condition_facets"] = []
    negative = CoverageVerdict.model_validate({"complete": True, "missing": [], "checks": [check]})
    assert not _guard_review_fulfillment(negative, [requirement], [chunk]).complete


def test_recovery_schedule_caps_and_repeated_fingerprints():
    from time import monotonic

    from app.modules.conversations.services.recovery_schedule import RecoverySchedule

    schedule = RecoverySchedule(monotonic() + 20)
    for i in range(3):
        assert schedule.admit(
            "search", requirement_ids=["R1"], fingerprint=str(i), expected_change="find rule"
        )
    assert not schedule.admit(
        "search", requirement_ids=["R1"], fingerprint="4", expected_change="find rule"
    )
    assert schedule.stop_reason == "action_limit_reached"
    assert schedule.admit(
        "delta_review", requirement_ids=["R1"], fingerprint="proof", expected_change="review"
    )
    assert not schedule.admit(
        "delta_review", requirement_ids=["R1"], fingerprint="new", expected_change="review"
    )


def test_structured_renderer_rejects_cross_requirement_proof():
    import json

    first = source("Tenants must submit a lease.")
    second = source("Factories must submit a permit.")
    second.metadata["reviewed_proof"][0]["requirement_id"] = "R2"
    draft = {
        "segments": [
            {
                "text": "Tenants submit a lease.",
                "requirement_ids": ["R1"],
                "proof_ids": [str(second.chunk_id)],
            }
        ]
    }
    assert (
        _render_structured_answer(json.dumps(draft), [first, second])[1]["status"]
        == "failed_verification"
    )


async def test_reviewed_proof_does_not_certify_unrelated_assertion():
    from app.core.config import ChatConfig
    from app.modules.conversations.grounding_service import GroundingService

    class Entailment:
        async def verify(self, assertions):
            return [
                "supported"
                if item["assertion"] == "Tenants must submit a lease."
                else "unsupported"
                for item in assertions
            ]

    chunk = source("ভাড়াটিয়ার ক্ষেত্রে ভাড়ার চুক্তিপত্র।")
    positive = await GroundingService(ChatConfig(), entailment=Entailment()).map_claims(
        "Tenants must submit a lease. [1]", [chunk]
    )
    assert positive.claims[0]["verification"] == "supported"
    for assertion in [
        "Owners must submit a lease.",
        "Factory owners must submit a fire safety certificate.",
    ]:
        negative = await GroundingService(ChatConfig(), entailment=Entailment()).map_claims(
            assertion + " [1]", [chunk]
        )
        assert negative.claims[0]["verification"] == "unsupported"


async def test_semantic_verifier_preserves_translation_and_rejects_scope_changes():
    import json
    from unittest.mock import AsyncMock

    from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
    from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage

    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps({"verdicts": ["supported", "unsupported", "unsupported", "unverified"]}),
        provider="test",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
        provider_version="1",
    )
    inputs = [
        {
            "assertion": "Tenants submit a lease.",
            "proof": [{"quote": "ভাড়াটিয়ার ক্ষেত্রে ভাড়ার চুক্তিপত্র।"}],
        },
        {"assertion": "Owners submit a lease.", "proof": [{"quote": "ভাড়াটিয়ার ক্ষেত্রে ভাড়ার চুক্তিপত্র।"}]},
        {"assertion": "The AY2026 amount is100.", "proof": [{"quote": "AY2025 amount100"}]},
        {"assertion": "The rate is100%.", "proof": [{"quote": "amount100"}]},
    ]
    assert await ClaimEntailmentService(llm).verify(inputs) == [
        "supported",
        "unsupported",
        "unsupported",
        "unverified",
    ]
    prompt = llm.generate.call_args.args[0][0].content
    assert "identical numbers" in prompt and "faithful translation" in prompt
