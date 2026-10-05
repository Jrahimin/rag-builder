"""Captured terminal regressions and complete deterministic transport/evaluation parity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import Settings
from app.modules.conversations import turn_resolution
from app.modules.conversations.grounding_service import GroundingResult, GroundingService
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.terminal_outcome import terminal_outcome
from app.modules.evaluation.ports import QualityHit
from tests.unit.modules.conversations import captured_fixture_helpers
from tests.unit.modules.conversations import test_chat_service as chat_test
from tests.unit.modules.conversations.captured_fixture_helpers import load_captured_fixture
from tests.unit.modules.conversations.test_chat_service import (
    CitedLLM,
    FakeRetrieval,
    _service,
)


@pytest.fixture(name="session")
def fixture_session():
    return chat_test.session.__wrapped__()


@pytest.fixture(name="conversation")
def fixture_conversation():
    return chat_test.conversation.__wrapped__()


@pytest.fixture(name="conversation_repository")
def fixture_conversation_repository(conversation):
    return chat_test.conversation_repository.__wrapped__(conversation)


@pytest.fixture(name="message_repository")
def fixture_message_repository():
    return chat_test.message_repository.__wrapped__()


pytestmark = pytest.mark.unit
CASES = load_captured_fixture("message_journey_failures_20261001_v1.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_saved_failure_has_truthful_terminal_stage(case):
    assert case in load_captured_fixture("message_journey_failures_20261001_v1.json")["cases"]
    result = terminal_outcome(
        reason="claim_verification_failed"
        if case["rejected_draft"]
        else "recovery_deadline_exceeded",
        supported_claims=0,
        partial=case["generation_ran"],
        missing_inputs=[],
        diagnostics={
            "knowledge_repair": case["repair"],
            "answer_draft": case["draft"],
            "normalized_scope": case["normalized_scope"],
            "generation_ran": case["generation_ran"],
        },
        coverage="partial" if case["generation_ran"] else "incomplete",
    )
    assert result.outcome == case["expected_terminal"]
    assert result.failure_stage == case["expected_stage"]


@pytest.mark.parametrize("outcome", ["answered", "partial", "verification_failed"])
async def test_regular_sse_evaluation_and_persisted_get_share_complete_result(
    session, conversation, conversation_repository, message_repository, monkeypatch, outcome
):
    monkeypatch.setattr(
        turn_resolution, "utc_reference_datetime", lambda: datetime(2026, 10, 1, tzinfo=UTC)
    )
    chunk = replace(
        (await FakeRetrieval().retrieve()).chunks[0],
        content="Customers can request a refund within 30 days of purchase.",
    )
    question = "What is the refund period?"
    rejected = outcome == "verification_failed"
    if rejected:

        async def reject(self, content, chunks, **kwargs):
            return GroundingResult(
                claims=[
                    {
                        "claim_id": "claim-1",
                        "text": content,
                        "claim_kind": "source_assertion",
                        "grounded": False,
                        "verification": "unsupported",
                        "verification_reason": "verifier_scope_mismatch",
                        "verification_method": "source_entailment",
                        "evidence": [],
                    }
                ],
                grounded=False,
                citation_coverage=0.0,
            )

        monkeypatch.setattr(GroundingService, "map_claims", reject)
    service = _service(
        session, conversation_repository, message_repository, CitedLLM(chunk.content + " [1]")
    )
    provenance = {"normalized_scope": {}, "rerank_status": "skipped"}
    if outcome == "partial":
        provenance["knowledge_repair"] = {
            "partial_answer": {
                "scope": "Refund period",
                "requirement_ids": ["R1"],
                "exclusions": ["Approval process"],
            },
            "requirements": [
                {"requirement_id": "R1", "description": "Refund period"},
                {"requirement_id": "R2", "description": "Approval process", "depends_on": ["R1"]},
            ],
            "coverage": {
                "quotes_validated": True,
                "checks": [
                    {"requirement_id": "R1", "supported": True, "fulfillment": "full"},
                    {"requirement_id": "R2", "supported": False, "fulfillment": "none"},
                ],
            },
        }
    service._retrieval = AsyncMock()
    service._retrieval.query_embedder = None
    service._retrieval.retrieve.return_value = ContextRetrievalResult(
        chunks=[chunk], diagnostics=provenance.copy()
    )
    regular = await service.send_message(conversation.id, MessageSendRequest(content=question))
    stream = [
        item
        async for item in service.stream_message(
            conversation.id, MessageSendRequest(content=question)
        )
    ]
    done = next(item for item in stream if isinstance(item, dict) and item.get("event") == "done")
    persisted = message_repository.add.call_args.args[0]
    get = MessageResponse.from_message(
        persisted, conversation_provider="echo", conversation_model="test"
    )
    settings = Settings(
        chat=service._chat_config, retrieval=service._retrieval_config, llm=service._llm_config
    )
    evaluation = await GroundedEvaluationAnswerAdapter(
        settings=settings, llm=CitedLLM(chunk.content + " [1]"), project_id=service._project_id
    ).answer_for_case(
        profile="semantic",
        request=MessageSendRequest(content=question),
        provenance=provenance,
        hits=[
            QualityHit(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                score=chunk.score,
                semantic_score=chunk.semantic_score,
                filename=chunk.filename,
                chunk_index=chunk.chunk_index,
            )
        ],
    )
    assert regular.assistant_message.content == get.content == evaluation.answer
    assert (
        regular.assistant_message.metadata["execution"]
        == get.metadata["execution"]
        == evaluation.execution
    )
    assert done["terminal_outcome"] == regular.assistant_message.terminal_outcome.model_dump(
        mode="json"
    )
    assert (
        regular.assistant_message.metadata["normalized_scope"] == get.metadata["normalized_scope"]
    )
    assert [c.assertion_id for c in get.claims] == [
        c.assertion_id for c in regular.assistant_message.claims
    ]
    assert get.metadata["execution"]["terminal"]["outcome"] == (outcome)
    if rejected:
        assert not get.claims and not get.citations
        assert get.metadata["attempted_claim_verification_counts"]["unsupported"] == 1
        assert get.metadata["published_claim_verification_counts"]["factual"] == 0


def test_requirement_contract_keeps_optional_origin_scope_and_span_identity():
    import uuid

    from app.modules.conversations.execution_contracts import EvidenceBundle, RequirementGraph
    from app.modules.conversations.ports import ContextChunk

    scope = turn_resolution.normalize_request_scope(
        "Compare and calculate for assessment year 2024-25"
    )
    assert scope.task_kind == "calculation"
    assert (
        turn_resolution.normalize_request_scope("Compare the rules without calculation").task_kind
        == "comparison"
    )
    graph = RequirementGraph.from_coverage(
        {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "Governing rule",
                    "origin": "explicit_user_request",
                },
                {
                    "requirement_id": "R2",
                    "description": "Extra example",
                    "origin": "optional_corroboration",
                    "depends_on": ["R1"],
                },
            ]
        },
        scope,
    )
    assert graph.requirements[0].required
    assert not graph.requirements[1].required
    assert graph.requirements[1].dependencies == ["R1"]
    assert graph.requirements[0].assigned_scope["requested_periods"][0]["start_year"] == 2024
    original = "intro rule proof conclusion"
    proof = "rule proof"
    chunk = ContextChunk(
        uuid.uuid4(),
        uuid.uuid4(),
        0,
        proof,
        0.9,
        "rule.txt",
        hashlib.sha256(proof.encode()).hexdigest(),
        char_start=106,
        char_end=116,
        metadata={
            "evidence_source_chunk_hash": hashlib.sha256(original.encode()).hexdigest(),
            "evidence_chunk_char_start": 6,
            "evidence_chunk_char_end": 16,
            "source_chunk_char_end": 100 + len(original),
        },
    )
    bundle = EvidenceBundle.from_chunk(chunk)
    assert bundle.source_hash == hashlib.sha256(original.encode()).hexdigest()
    assert bundle.span_hash == hashlib.sha256(proof.encode()).hexdigest() != bundle.source_hash
    assert (bundle.source_char_start, bundle.local_char_start, bundle.local_char_end) == (
        100,
        6,
        16,
    )
    assert original[bundle.local_char_start : bundle.local_char_end] == bundle.text


async def test_later_verification_timeout_preserves_completed_attempts_and_proof(
    session, conversation, conversation_repository, message_repository, monkeypatch
):
    from app.platform.providers.errors import ProviderTimeoutError

    source = (await FakeRetrieval().retrieve()).chunks[0]
    statement = "Customers can request a refund within 30 days of purchase."
    source = replace(
        source,
        metadata={
            "reviewed_proof": [
                {"requirement_id": "R1", "quote": source.content, "fulfillment": "full"}
            ]
        },
    )
    mapping = AsyncMock(
        side_effect=[
            GroundingResult(
                claims=[
                    {
                        "claim_id": "C1",
                        "assertion_id": "A1",
                        "text": statement,
                        "verification": "unsupported",
                        "verification_reason": "missing_citation",
                        "evidence": [],
                    },
                    {
                        "claim_id": "C2",
                        "assertion_id": "A2",
                        "text": source.content,
                        "verification": "supported",
                        "evidence": [{"chunk_id": str(source.chunk_id), "citation_index": 1}],
                    },
                ],
                grounded=False,
                citation_coverage=0.0,
            ),
            ProviderTimeoutError(
                "deadline",
                provider_name="echo",
                context={"reason": "request_deadline_exceeded", "phase": "claim_verification"},
            ),
        ]
    )
    monkeypatch.setattr(GroundingService, "map_claims", mapping)
    draft = json.dumps(
        {
            "segments": [
                {
                    "assertion_id": "A1",
                    "text": statement,
                    "requirement_ids": ["R1"],
                    "proof_ids": [str(source.chunk_id)],
                },
                {
                    "assertion_id": "A2",
                    "text": source.content,
                    "requirement_ids": ["R1"],
                    "proof_ids": [str(source.chunk_id)],
                },
            ]
        }
    )
    service = _service(session, conversation_repository, message_repository, CitedLLM(draft))
    service._retrieval = AsyncMock()
    service._retrieval.query_embedder = None
    service._retrieval.retrieve.return_value = ContextRetrievalResult(
        chunks=[source], diagnostics={}
    )
    request = MessageSendRequest(content="What is the refund period?")
    turn = await service.send_message(conversation.id, request)
    assert turn.assistant_message.terminal_outcome.outcome == "timed_out"
    assert not turn.assistant_message.claims and not turn.assistant_message.citations
    retained = service._runner.deadline_result(
        request, reason="request_deadline_exceeded", failure_phase="claim_verification"
    ).finalization
    assert len(retained.evidence) == 1
    assert [c.verdict for c in retained.rejected_attempts] == [
        "unsupported",
        "supported",
        "unverified",
        "unverified",
    ]
    assert retained.correction_attempts[-1]["status"] == "timed_out"
    assert "operator_diagnostic" not in turn.assistant_message.metadata
    assert statement not in turn.assistant_message.content


@pytest.mark.parametrize("name", captured_fixture_helpers.CAPTURE_FIXTURE_SHA256)
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["LF", "CRLF"])
def test_captured_fixture_integrity_survives_checkout_line_endings(
    name, newline, tmp_path, monkeypatch
):
    expected = load_captured_fixture(name)
    payload = (captured_fixture_helpers.FIXTURE_ROOT / name).read_bytes()
    payload = payload.replace(b"\r\n", b"\n").replace(b"\n", newline)
    (tmp_path / name).write_bytes(payload)
    monkeypatch.setattr(captured_fixture_helpers, "FIXTURE_ROOT", tmp_path)
    assert load_captured_fixture(name) == expected


@pytest.mark.parametrize("name", captured_fixture_helpers.CAPTURE_FIXTURE_SHA256)
def test_captured_fixture_integrity_rejects_changed_test_data(name, tmp_path, monkeypatch):
    data = load_captured_fixture(name)
    if "live_calls_made" in data:
        data["live_calls_made"] = True
    else:
        data["cases"][0]["repair"]["stop_reason"] = "changed"
    (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(captured_fixture_helpers, "FIXTURE_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="Captured fixture changed"):
        load_captured_fixture(name)
