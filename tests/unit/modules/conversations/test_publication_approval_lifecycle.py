"""Typed verifier publication approval remains bound across request corrections."""

import asyncio
import json
import uuid
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from app.modules.conversations.grounding_service import GroundingService, _answer_segments
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.message_execution_runner_service import (
    _publication_claim_complete,
)
from app.platform.providers.request_work import current_request_purpose
from tests.unit.modules.conversations.test_omitted_deliverable_correction import omitted_journey
from tests.unit.modules.conversations.test_phase2_completion import execute_contract

pytestmark = pytest.mark.unit
TRANSPORTS = ["regular", "sse", "get", "evaluation"]


@pytest.mark.parametrize("transport", ["regular", "sse"])
async def test_supported_incomplete_draft_can_correct_to_fresh_reviewed_scope(transport):
    service, provider, conversation, repository, question, requests = omitted_journey()
    original_answer = provider.answer
    original_generate = provider.generate

    def answer():
        payload = json.loads(original_answer())
        payload["segments"][0]["text"] += " and"
        return json.dumps(payload)

    provider.answer = answer

    async def generate(messages, **kwargs):
        result = await original_generate(messages, **kwargs)
        if current_request_purpose() == "claim_verification":
            assertions = json.loads(messages[1].content)
            payload = json.loads(result.content)
            for item, verdict in zip(assertions, payload["verdicts"], strict=True):
                if item["assertion"].endswith(" and"):
                    verdict.update(
                        status="supported", failed_dimensions=[], publication_complete=False
                    )
                else:
                    verdict["publication_complete"] = True
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert "18 months" in answer["content"]
    assert any("R1" in claim["requirement_ids"] for claim in answer["claims"])
    assert len(requests) == 2


def approval_journey(complete, failure=None):
    values = omitted_journey(failure=failure)
    provider = values[1]
    answer_original = provider.answer

    def answer():
        payload = json.loads(answer_original())
        if complete:
            payload["segments"][0]["text"] = payload["segments"][0]["text"].rstrip(".")
        return json.dumps(payload)

    provider.answer = answer
    generate_original = provider.generate

    async def generate(messages, **kwargs):
        result = await generate_original(messages, **kwargs)
        if current_request_purpose() == "claim_verification":
            assertions = json.loads(messages[1].content)
            payload = json.loads(result.content)
            scopes = {
                atom.rstrip(".")
                for check in provider.review()[1]["checks"]
                for atom in _answer_segments(check["answerable_scope"])
            }
            for item, verdict in zip(assertions, payload["verdicts"], strict=True):
                if any(proof.get("requirement_id") == "R1" for proof in item["proof"]):
                    assert item["assertion"].rstrip(".") in scopes
                    verdict.update(
                        status="supported", failed_dimensions=[], publication_complete=complete
                    )
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    return values


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("complete", [False, True], ids=["incomplete", "approved_unpunctuated"])
@pytest.mark.parametrize("failure", [None, "reject", "unavailable", "foreign", "timeout"])
async def test_publication_verdict_survives_correction_and_failure(transport, complete, failure):
    service, provider, conversation, repository, question, requests = approval_journey(
        complete, failure
    )
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    first_claims = [c for c in answer["claims"] if "R1" in c["requirement_ids"]]
    if complete:
        assert first_claims and "18 months" in answer["content"]
        assert "incorporation year or following year" in answer["content"]
        assert all(c["verification"] == "supported" for c in first_claims)
        assert any(n["kind"] == "unresolved_applicability" for n in answer["notices"])
    else:
        assert first_claims == [] and "18 months" not in answer["content"]
        assert "R1" in answer["terminal_outcome"]["unresolved_requirement_ids"]
    if failure is None:
        assert "15 months" in answer["content"]
        assert "Registrar extension" in answer["content"]
        assert any("R2" in c["requirement_ids"] for c in answer["claims"])
    else:
        assert "15 months" not in answer["content"]
        assert "R2" in answer["terminal_outcome"]["unresolved_requirement_ids"]
    assert len(requests) >= 2
    # Evaluation owns another runner; its actual provider trace is the common proof.
    assert (
        len(
            [
                r
                for batch in requests
                for r in batch
                if any(p.get("requirement_id") == "R2" for p in r["proof"])
            ]
        )
        <= 4
    )
    if transport != "evaluation":
        assert service._work.counts["semantic_repairs"] == 1


@pytest.mark.parametrize("complete", [False, True])
async def test_publication_approval_checkpoint_survives_cancelled_correction(complete):
    service, provider, conversation, repository, question, requests = approval_journey(
        complete, "cancelled"
    )
    with pytest.raises(asyncio.CancelledError):
        await execute_contract(service, provider, conversation, repository, question, "regular")
    checkpoint = service._runner.deadline_result(
        MessageSendRequest(content=question),
        reason="request_deadline_exceeded",
        failure_phase="verification",
    )
    assert ("18 months" in checkpoint.content) is complete
    assert ("incorporation year or following year" in checkpoint.content) is complete
    assert "15 months" not in checkpoint.content
    assert len(requests) == 2 and service._work.counts["semantic_repairs"] == 1


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("mutation", ["display", "assertion", "proof"])
async def test_first_pass_identity_mutation_cannot_gain_approval_after_correction(
    transport, mutation, monkeypatch
):
    service, provider, conversation, repository, question, requests = approval_journey(True)
    original = GroundingService.map_claims

    async def map_claims(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        for claim in result.claims:
            if "R1" in claim["requirement_ids"]:
                if mutation == "display":
                    claim["text"] += " An unverified additional rule applies."
                elif mutation == "assertion":
                    claim["assertion_text"] = (
                        claim.get("assertion_text") or claim["text"]
                    ) + " An unverified additional rule applies."
                else:
                    claim["evidence"][0]["evidence_span_hash"] = "foreign-span-hash"
        return result

    monkeypatch.setattr(GroundingService, "map_claims", map_claims)
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert all("R1" not in c["requirement_ids"] for c in answer["claims"])
    assert "18 months" not in answer["content"] and "unverified additional" not in answer["content"]
    assert "R1" in answer["terminal_outcome"]["unresolved_requirement_ids"]
    assert requests


async def test_produced_publication_approval_rejects_owner_scope_source_and_missing_registry(
    monkeypatch,
):
    service, provider, conversation, repository, question, requests = approval_journey(True)
    original = GroundingService.map_claims
    captured = []

    async def map_claims(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        captured.extend(deepcopy(c) for c in result.claims if "R1" in c["requirement_ids"])
        return result

    monkeypatch.setattr(GroundingService, "map_claims", map_claims)
    await execute_contract(service, provider, conversation, repository, question, "regular")
    prepared = service._runner._active_prepared
    claim = captured[-1]
    assert requests
    assert _publication_claim_complete(claim, prepared)
    approval = next(iter(prepared.publication_approvals.values()))
    with pytest.raises(FrozenInstanceError):
        approval.complete = False
    assert not _publication_claim_complete(claim, replace(prepared, publication_owner=uuid.uuid4()))
    assert not _publication_claim_complete(claim, replace(prepared, publication_approvals={}))
    assert not _publication_claim_complete(
        claim, replace(prepared, evidence_scope={"index_build_id": "foreign-build"})
    )
    different = replace(
        prepared,
        execution_intent=prepared.intent.model_copy(
            update={
                "scope": prepared.intent.scope.model_copy(update={"subject": "A different request"})
            }
        ),
    )
    assert not _publication_claim_complete(claim, different)
    changed = deepcopy(claim)
    changed["evidence"][0]["chunk_id"] = str(uuid.uuid4())
    assert not _publication_claim_complete(changed, prepared)


async def test_fresh_request_cannot_reuse_another_requests_publication_approval(monkeypatch):
    first = approval_journey(True)
    second = approval_journey(False)
    original = GroundingService.map_claims
    captured = []

    async def map_claims(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        captured.extend(deepcopy(c) for c in result.claims if "R1" in c["requirement_ids"])
        return result

    monkeypatch.setattr(GroundingService, "map_claims", map_claims)
    await execute_contract(*first[:5], "regular")
    old = first[0]._runner._active_prepared
    old_claim = captured[-1]
    answer = await execute_contract(*second[:5], "regular")
    fresh = second[0]._runner._active_prepared
    assert old.publication_owner != fresh.publication_owner
    assert all("R1" not in claim["requirement_ids"] for claim in answer["claims"])
    assert not _publication_claim_complete(old_claim, fresh)
    copied = replace(fresh, publication_approvals=dict(old.publication_approvals))
    assert not _publication_claim_complete(old_claim, copied)
    assert _publication_claim_complete(old_claim, old)
