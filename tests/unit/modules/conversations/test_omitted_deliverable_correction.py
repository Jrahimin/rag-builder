"""An omitted deliverable uses the existing correction and strict proof verifier."""

import asyncio
import json
import time
from dataclasses import replace

import pytest

from app.modules.conversations.execution_contracts import EvidenceBundle
from app.modules.conversations.grounding_service import _answer_segments
from app.modules.conversations.schemas.message import MessageSendRequest
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.request_work import current_request_purpose
from tests.unit.modules.conversations.test_phase2_completion import execute_contract
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


def omitted_journey(failure=None, guard=None, omit=True):
    service, provider, conversation, repository, question = journey()
    review_original = provider.review

    def review():
        requirements, coverage = review_original()
        coverage["checks"][0]["answerable_scope"] += (
            " If held within that time, another AGM is not needed in the incorporation "
            "year or following year."
        )
        coverage["checks"][1]["answerable_scope"] += (
            " The source separately permits a Registrar extension for subsequent AGMs, "
            "excluding the first AGM."
        )
        coverage["checks"][1]["evidence"][0]["end_line"] = 9
        return requirements, coverage

    provider.review = review
    answer_original = provider.answer

    def answer():
        value = json.loads(answer_original())
        if omit:
            value["segments"] = value["segments"][:1]
        value["notices"] = [
            {"kind": "documentary_scope", "proof_ids": [str(provider.source.chunk_id)]},
            {"kind": "unresolved_applicability", "proof_ids": [str(provider.source.chunk_id)]},
        ]
        return json.dumps(value)

    provider.answer = answer
    generated = provider.generate
    verification_requests = []

    async def generate(messages, **kwargs):
        purpose = current_request_purpose()
        if purpose == "claim_verification":
            assertions = json.loads(messages[1].content)
            verification_requests.append(assertions)
            correcting = len(verification_requests) > 1
            if correcting and failure == "timeout":
                raise ProviderTimeoutError(
                    "controlled correction cutoff",
                    provider_name="fixture",
                    context={"reason": "request_deadline_exceeded"},
                )
            if correcting and failure == "cancelled":
                raise asyncio.CancelledError("controlled cancelled correction")
            if correcting and failure == "unavailable":
                raise ProviderError("controlled unavailable verifier", provider_name="fixture")
        result = await generated(messages, **kwargs)
        if purpose == "claim_verification":
            allowed = {
                atom
                for check in provider.review()[1]["checks"]
                for atom in _answer_segments(check["answerable_scope"])
            }
            payload = json.loads(result.content)
            for assertion, row in zip(assertions, payload["verdicts"], strict=True):
                row.update(
                    status="supported" if assertion["assertion"] in allowed else "unsupported",
                    failed_dimensions=[] if assertion["assertion"] in allowed else ["scope"],
                )
            result = replace(result, content=json.dumps(payload))
            if guard == "deadline":
                service._work.deadline = time.perf_counter() - 1
        if purpose == "claim_verification" and failure in {"reject", "foreign", "protocol"}:
            payload = json.loads(result.content)
            for row in payload["verdicts"]:
                if len(verification_requests) > 1 or failure == "protocol":
                    if failure == "reject":
                        row.update(status="unsupported", failed_dimensions=["scope"])
                    else:
                        row["scope_hash"] = "foreign-scope"
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    prepare_original = service._runner.prepare

    async def prepare(**kwargs):
        prepared = await prepare_original(**kwargs)
        if guard in {"prerequisite", "calculation", "personal", "optional", "applicability"}:
            requirement = prepared.requirements.requirements[1]
            values = {
                "prerequisite": {"deliverable": False},
                "calculation": {"task_kind": "calculation"},
                "personal": {"task_kind": "personal_eligibility"},
                "optional": {"required": False},
                "applicability": {"depends_on": ["unknown-governing-scope"]},
            }[guard]
            prepared.requirements.requirements[1] = requirement.model_copy(update=values)
        if guard in {"partial", "unreviewed", "foreign", "no_proof"}:
            rows = prepared.selected[0].metadata["reviewed_proof"]
            for row in rows:
                if row["requirement_id"] == "R2":
                    if guard == "partial":
                        row["fulfillment"] = "partial"
                    elif guard == "unreviewed":
                        row.pop("fulfillment")
                    elif guard == "foreign":
                        row["requirement_id"] = "foreign-requirement"
                    else:
                        row["quote"] = "Unattested invented proof"
            object.__setattr__(
                prepared, "bundles", tuple(EvidenceBundle.from_chunk(c) for c in prepared.selected)
            )
        if guard == "spent":
            assert service._work.claim_correction("semantic")
        return prepared

    service._runner.prepare = prepare
    return service, provider, conversation, repository, question, verification_requests


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
async def test_omitted_full_proof_requirement_is_corrected_and_verified(transport):
    service, provider, conversation, repository, question, requests = omitted_journey()
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert "incorporation year or following year" in answer["content"]
    assert (
        "Registrar extension" in answer["content"]
        and "excluding the first AGM" in answer["content"]
    )
    assert answer["terminal_outcome"]["unresolved_requirement_ids"] == []
    assert {r for c in answer["claims"] for r in c["requirement_ids"]} == {"R1", "R2"}
    assert all(c["verification"] == "supported" for c in answer["claims"])
    assert service._work.counts["semantic_repairs"] == 1
    assert len(requests) == 2 and requests[1]
    assert requests[1][0]["proof"][0]["requirement_id"] == "R2"
    assert "পনের" in requests[1][0]["proof"][0]["quote"]
    assert requests[0][0]["proof"][0]["chunk_id"] == requests[1][0]["proof"][0]["chunk_id"]
    assert any(n["kind"] == "unresolved_applicability" for n in answer["notices"])
    assert "current law" not in answer["content"].lower()


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("failure", ["reject", "unavailable", "timeout", "foreign"])
async def test_failed_omission_correction_retains_supported_independent_rule(transport, failure):
    service, provider, conversation, repository, question, requests = omitted_journey(
        failure=failure
    )
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert "18 months" in answer["content"] and "15 months" not in answer["content"]
    assert answer["terminal_outcome"]["supported_requirement_ids"] == ["R1"]
    assert answer["terminal_outcome"]["unresolved_requirement_ids"] == ["R2"]
    assert answer["claims"] and all(c["requirement_ids"] == ["R1"] for c in answer["claims"])
    assert len(requests) >= 2 and service._work.counts["semantic_repairs"] == 1


@pytest.mark.parametrize(
    "guard",
    [
        "partial",
        "unreviewed",
        "foreign",
        "no_proof",
        "prerequisite",
        "calculation",
        "personal",
        "optional",
        "applicability",
        "spent",
        "deadline",
    ],
)
async def test_ineligible_omitted_requirement_does_not_spend_extra_correction(guard):
    service, provider, conversation, repository, question, requests = omitted_journey(guard=guard)
    answer = await execute_contract(
        service, provider, conversation, repository, question, "regular"
    )
    assert "18 months" in answer["content"] and "15 months" not in answer["content"]
    assert len(requests) == 1
    assert service._work.counts["semantic_repairs"] == int(guard == "spent")


async def test_complete_draft_does_not_add_correction_or_verifier_call():
    service, provider, conversation, repository, question, requests = omitted_journey(omit=False)
    answer = await execute_contract(
        service, provider, conversation, repository, question, "regular"
    )
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert len(requests) == 1 and service._work.counts["semantic_repairs"] == 0


async def test_initial_verifier_protocol_failure_cannot_trigger_omission_repair():
    service, provider, conversation, repository, question, requests = omitted_journey(
        failure="protocol"
    )
    answer = await execute_contract(
        service, provider, conversation, repository, question, "regular"
    )
    assert answer["grounded"] is False
    assert requests
    assert service._work.counts["semantic_repairs"] == 0


async def test_cancelled_omission_verification_keeps_supported_checkpoint_and_scope_notices():
    service, provider, conversation, repository, question, requests = omitted_journey(
        failure="cancelled"
    )
    with pytest.raises(asyncio.CancelledError):
        await execute_contract(service, provider, conversation, repository, question, "regular")
    checkpoint = service._runner.deadline_result(
        MessageSendRequest(content=question),
        reason="request_deadline_exceeded",
        failure_phase="verification",
    )
    assert "18 months" in checkpoint.content and "15 months" not in checkpoint.content
    assert checkpoint.finalization.terminal.supported_requirement_ids == ["R1"]
    assert checkpoint.finalization.terminal.unresolved_requirement_ids == ["R2"]
    assert any(n["kind"] == "unresolved_applicability" for n in checkpoint.metadata["notices"])
    assert len(requests) == 2 and service._work.counts["semantic_repairs"] == 1
