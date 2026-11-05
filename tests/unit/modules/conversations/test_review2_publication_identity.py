"""Contextual verifier identities survive publication without permitting mutation."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.grounding_service import GroundingService, _plain_claim_text
from app.modules.conversations.schemas.message import MessageResponse
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.modules.conversations.services.message_execution_runner_service import (
    _publication_claim_complete,
)
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.request_work import current_request_purpose
from tests.unit.modules.conversations.test_phase2_completion import execute_contract, source
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


def canonical(text):
    return " ".join(_plain_claim_text(text).split())


def bound_result(items, allowed):
    return ChatCompletionResult(
        content=json.dumps(
            {
                "version": "claim.entailment.v2",
                "verdicts": [
                    {
                        "assertion_id": item["assertion_id"],
                        "proof_hashes": item["proof_hashes"],
                        "scope_hash": item["scope_hash"],
                        "status": "supported"
                        if canonical(item["assertion"]) in allowed
                        else "unsupported",
                        "failed_dimensions": [],
                        "evidence_binding": "controlled contextual fixture",
                        "publication_complete": True,
                    }
                    for item in items
                ],
            }
        ),
        provider="fixture",
        model="fixture",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )


async def test_raw_review_probe_keeps_display_and_context_bindings_separate():
    import uuid
    from types import SimpleNamespace

    from app.modules.conversations.execution_contracts import TurnIntent
    from app.modules.conversations.services.message_execution_runner_service import (
        _publication_context,
    )
    from app.modules.conversations.turn_resolution import normalize_request_scope

    text = "Refunds are available for thirty days."
    allowed = {text, f"### Refund policy {text}"}
    llm = AsyncMock()
    llm.supports_output_contract = True
    llm.generate.side_effect = lambda messages, **kwargs: bound_result(
        json.loads(messages[1].content), allowed
    )
    grounding = GroundingService(ChatConfig(), entailment=ClaimEntailmentService(llm))
    chunk = source("Company refund policy.\n" + text + "\nA receipt is required.")
    prepared = SimpleNamespace(
        grounding=grounding,
        selected=[chunk],
        publication_owner=uuid.uuid4(),
        intent=TurnIntent.from_scope(normalize_request_scope("Explain the refund policy")),
        evidence_scope={},
        retrieval_diagnostics={},
        publication_approvals={},
    )
    prepared.publication_context = _publication_context(prepared)
    for answer in [f"{text} [1]", f"### Refund policy\n\n1. {text} [1]"]:
        result = await grounding.map_claims(
            answer,
            [chunk],
            canonical_scope=prepared.intent.scope.model_dump(mode="json"),
            publication_context=prepared.publication_context,
        )
        for approval in grounding.publication_approvals:
            prepared.publication_approvals.setdefault(approval.binding, approval)
        assert len(result.claims) == 1
        claim = result.claims[0]
        assert claim["verification"] == "supported"
        assert grounding.publication_completeness[claim["assertion_id"]] is True
        assert _publication_claim_complete(claim, prepared)
        assert not _publication_claim_complete(
            {**claim, "text": text + " An extra rule applies."}, prepared
        )
        assert not _publication_claim_complete({**claim, "assertion_text": ""}, prepared)
        if claim["assertion_text"] is not None:
            assert not _publication_claim_complete({**claim, "assertion_text": None}, prepared)


def representations(context, scopes):
    if context == "flat":
        displays, prefix = scopes, ""
    elif context == "heading":
        displays, prefix = scopes, "### AGM deadlines"
    elif context == "list":
        displays, prefix = [f"- {scope}" for scope in scopes], "AGM deadlines:"
    else:
        displays = [
            f"| {label} | {scope} |"
            for label, scope in zip(["First AGM", "Subsequent AGM"], scopes, strict=True)
        ]
        prefix = "| Rule | Deadline |"
    raw_blocks = [f"{text} [1]" for text in displays]
    if prefix:
        separator = "\n|---|---|\n" if context == "table" else "\n\n"
        raw_blocks = [prefix + separator + body for body in raw_blocks]
    contextual = [f"{prefix} {display}".strip() for display in displays]
    # Typed segments are indivisible assertions; carry context inside the same
    # atom rather than adding an independently asserted structural heading.
    typed = contextual
    return "\n\n".join(raw_blocks), typed, {canonical(text) for text in contextual}


def contextual_journey(representation, context, *, qualifier=None):
    service, provider, conversation, repository, question = journey()
    requirements, coverage = provider.review()
    scopes = [item["answerable_scope"] for item in coverage["checks"]]
    if qualifier:
        # Fresh controlled evidence supplies the material qualifier in every
        # cited proof; captured source bytes and locator claims stay untouched.
        qualified_scopes = [f"{qualifier}: {text}" for text in scopes]
        controlled = source("\n\n".join(qualified_scopes))
        controlled = replace(
            controlled,
            project_id=conversation.project_id,
            metadata={
                "project_id": str(conversation.project_id),
                "index_build_id": provider.source.metadata["index_build_id"],
                "source_metadata_generation": provider.source.metadata[
                    "source_metadata_generation"
                ],
            },
        )
        provider.source = controlled
        service._retrieval.retrieve.return_value = replace(
            service._retrieval.retrieve.return_value, chunks=[controlled]
        )
        qualified_review = {
            **coverage,
            "checks": [
                {
                    **check,
                    "answerable_scope": text,
                    "evidence": [{"chunk_id": str(controlled.chunk_id), "quote": text}],
                }
                for check, text in zip(coverage["checks"], qualified_scopes, strict=True)
            ],
        }
        provider.review = lambda: (requirements, qualified_review)
    raw, typed, allowed = representations(context, scopes)
    if qualifier:
        original, qualified = {
            "heading": ("### AGM deadlines", f"### {qualifier}"),
            "list": ("AGM deadlines:", f"{qualifier}:"),
            "table": ("| Rule | Deadline |", f"| {qualifier} | Deadline |"),
        }[context]
        raw = raw.replace(original, qualified)
        typed = [text.replace(original, qualified) for text in typed]
        allowed = {text.replace(original, qualified) for text in allowed}
    original_generate = provider.generate
    original_answer = json.loads(provider.answer())
    for row, text in zip(original_answer["segments"], typed, strict=True):
        row["text"] = text
    provider.answer = lambda: json.dumps(original_answer)
    verified_inputs = []

    async def generate(messages, **kwargs):
        purpose = current_request_purpose()
        if purpose == "claim_verification":
            items = json.loads(messages[1].content)
            verified_inputs.extend(items)
            return bound_result(items, allowed)
        if representation == "raw" and purpose == "answer_generation":
            return ChatCompletionResult(
                content=raw,
                provider="fixture",
                model="fixture",
                provider_version="1",
                finish_reason="stop",
                usage=ChatUsage(1, 1),
            )
        return await original_generate(messages, **kwargs)

    provider.generate = generate
    if representation == "raw":
        original_prepare = service._runner.prepare

        async def legacy_prepare(*args, **kwargs):
            prepared = await original_prepare(*args, **kwargs)
            # A controlled legacy evidence shape uses ordinary whole-passage
            # proof, while the actual v2 verifier still binds its hash and scope.
            # The captured source fixture and production preparation stay intact.
            selected = [
                replace(
                    chunk,
                    metadata={
                        key: value
                        for key, value in chunk.metadata.items()
                        if key != "reviewed_proof"
                    },
                )
                for chunk in prepared.selected
            ]
            return replace(prepared, selected=selected, generation_mode="prose")

        service._runner.prepare = legacy_prepare
    return (service, provider, conversation, repository, question), allowed, verified_inputs


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("representation", ["raw", "typed"])
@pytest.mark.parametrize("context", ["flat", "heading", "list", "table"])
@pytest.mark.parametrize("mutation", [None, "display", "assertion"])
async def test_contextual_identity_finalization_checkpoint_and_persistence(
    transport, representation, context, mutation, monkeypatch
):
    parts, allowed, verified_inputs = contextual_journey(representation, context)
    service, provider, conversation, repository, question = parts
    original_map = GroundingService.map_claims
    prepared_turns = []
    checkpoint_claims = []
    original_checkpoint = service._runner._checkpoint_claims

    async def contextual_map(self, content, chunks, **kwargs):
        result = await original_map(self, content, chunks, **kwargs)
        assert len(result.claims) == 2
        assert all(claim["verification"] == "supported" for claim in result.claims), result.claims
        assert all(
            self.publication_completeness[claim["assertion_id"]] is True for claim in result.claims
        )
        if representation == "raw" and context != "flat":
            assert all(claim["assertion_text"] != claim["text"] for claim in result.claims)
        if mutation:
            for claim in result.claims:
                field = "text" if mutation == "display" else "assertion_text"
                claim[field] = (
                    claim.get(field) or claim["text"]
                ) + " An unverified extra rule applies."
        return result

    def checkpoint(prepared, claims):
        prepared_turns.append(prepared)
        original_checkpoint(prepared, claims)
        saved = service._runner._verified_checkpoint
        if saved is not None:
            checkpoint_claims.append(saved.restore(prepared.intent.scope).claims)

    monkeypatch.setattr(GroundingService, "map_claims", contextual_map)
    service._runner._checkpoint_claims = checkpoint
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert verified_inputs and all(
        canonical(item["assertion"]) in allowed for item in verified_inputs
    )
    assert prepared_turns
    if mutation:
        assert not checkpoint_claims
        assert not answer["claims"] and not answer["citations"]
        assert answer["terminal_outcome"]["outcome"] == "verification_failed"
        assert "unverified extra rule" not in answer["content"]
    else:
        assert answer["grounded"] is True and len(answer["claims"]) == 2
        assert answer["citations"] and checkpoint_claims
        assert all(
            _publication_claim_complete(claim, prepared_turns[-1]) for claim in answer["claims"]
        )
        assert {claim["text"] for claim in checkpoint_claims[0]} == {
            claim["text"] for claim in answer["claims"]
        }
        assert "18 months" in answer["content"] and "15 months" in answer["content"]


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("representation", ["raw", "typed"])
@pytest.mark.parametrize("context", ["heading", "list", "table"])
async def test_contextual_verified_checkpoint_survives_timeout(
    transport, representation, context, monkeypatch
):
    parts, _allowed, _verified_inputs = contextual_journey(representation, context)
    service, provider, conversation, repository, question = parts
    service._chat_config = service._runner._chat_config = ChatConfig(
        execution_policy="bounded_v2", bounded_recovery_enabled=True
    )
    original_verify = service._runner._verify_claims

    async def interrupted(*args, **kwargs):
        await original_verify(*args, **kwargs)
        raise TimeoutError("controlled timeout after contextual verification")

    service._runner._verify_claims = interrupted
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["terminal_outcome"]["outcome"] == "partial"
    assert answer["grounded"] is True and len(answer["claims"]) == 2
    assert answer["citations"]
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert answer["metadata"]["verified_checkpoint"]


QUALIFIERS = [
    pytest.param("Private companies only", id="category"),
    pytest.param("For fiscal year 2026-27", id="period"),
    pytest.param("Except companies in liquidation", id="exception"),
    pytest.param("All months are calendar months", id="unit"),
]


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("representation", ["raw", "typed"])
@pytest.mark.parametrize("context", ["heading", "list", "table"])
@pytest.mark.parametrize("termination", ["normal", "retained", "length", "timeout"])
@pytest.mark.parametrize("qualifier", QUALIFIERS)
async def test_verified_qualifier_survives_final_publication(
    transport, representation, context, termination, qualifier
):
    parts, allowed, verified_inputs = contextual_journey(
        representation, context, qualifier=qualifier
    )
    service, provider, _conversation, repository, _question = parts
    if termination == "retained":
        allowed.difference_update(text for text in tuple(allowed) if "15 months" in text)
    original_generate = provider.generate
    unverified_surrounding = "Public companies also qualify"

    async def generate(messages, **kwargs):
        result = await original_generate(messages, **kwargs)
        if current_request_purpose() == "answer_generation":
            if termination == "length":
                result = replace(result, finish_reason="length")
            if termination == "retained":
                if representation == "raw":
                    content = result.content + (
                        f"\n\n### {unverified_surrounding}\n\n"
                        "An invented extra obligation applies. [1]"
                    )
                else:
                    draft = json.loads(result.content)
                    draft["segments"].append(
                        {
                            "text": (
                                f"{unverified_surrounding}: An invented extra obligation applies."
                            ),
                            "requirement_ids": ["R2"],
                            "proof_ids": [str(provider.source.chunk_id)],
                        }
                    )
                    content = json.dumps(draft)
                result = replace(result, content=content)
        return result

    provider.generate = generate
    if termination == "length" and transport == "sse":
        original_provider_stream = provider.stream

        async def length_stream(*args, **kwargs):
            async for event in original_provider_stream(*args, **kwargs):
                yield (
                    replace(event, finish_reason="length")
                    if event.finish_reason is not None
                    else event
                )

        provider.stream = length_stream
    if termination == "timeout":
        service._chat_config = service._runner._chat_config = ChatConfig(
            execution_policy="bounded_v2", bounded_recovery_enabled=True
        )
        original_verify = service._runner._verify_claims

        async def interrupted(*args, **kwargs):
            await original_verify(*args, **kwargs)
            raise TimeoutError("controlled timeout after qualified verification")

        service._runner._verify_claims = interrupted
    done_events = []
    if transport == "sse":
        original_stream = service.stream_message

        async def stream(*args, **kwargs):
            async for event in original_stream(*args, **kwargs):
                if isinstance(event, dict) and event.get("event") == "done":
                    done_events.append(event)
                yield event

        service.stream_message = stream
    answer = await execute_contract(*parts, transport)
    assert qualifier in answer["content"]
    assert "18 months" in answer["content"]
    assert answer["grounded"] is True
    assert len(answer["claims"]) == (1 if termination == "retained" else 2)
    assert answer["citations"]
    assert all(claim["verification"] == "supported" for claim in answer["claims"])
    assert all(
        qualifier in (claim["assertion_text"] or claim["text"]) for claim in answer["claims"]
    )
    if representation == "raw":
        assert all(qualifier not in claim["text"] for claim in answer["claims"])
    assert verified_inputs
    assert all(
        qualifier in item["assertion"]
        for item in verified_inputs
        if "18 months" in item["assertion"]
    )
    if termination == "retained":
        assert "15 months" not in answer["content"]
        assert unverified_surrounding not in answer["content"]
        assert "invented extra obligation" not in answer["content"]
        assert answer["terminal_outcome"]["outcome"] == "partial"
    elif termination == "timeout":
        assert answer["metadata"]["verified_checkpoint"]
        assert answer["terminal_outcome"]["outcome"] == "partial"
    elif termination == "length":
        assert answer["terminal_outcome"]["outcome"] == "partial"
    saved = MessageResponse.from_message(repository.add.call_args.args[0]).model_dump(mode="json")
    for field in ["content", "claims", "citations"]:
        assert answer[field] == saved[field]
        if transport == "sse":
            assert done_events[-1][field] == saved[field]


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("representation", ["raw", "typed"])
@pytest.mark.parametrize("context", ["heading", "list", "table"])
async def test_removed_verified_qualifier_cannot_publish_or_enter_checkpoint(
    transport, representation, context, monkeypatch
):
    qualifier = "Private companies only"
    parts, _allowed, _inputs = contextual_journey(representation, context, qualifier=qualifier)
    service, _provider, _conversation, _repository, _question = parts
    original_map = GroundingService.map_claims

    async def dropped_qualifier(self, *args, **kwargs):
        result = await original_map(self, *args, **kwargs)
        assert all(claim["verification"] == "supported" for claim in result.claims)
        for claim in result.claims:
            field = "assertion_text" if representation == "raw" else "text"
            assert qualifier in claim[field]
            claim[field] = claim[field].replace(qualifier, "").strip()
        return result

    monkeypatch.setattr(GroundingService, "map_claims", dropped_qualifier)
    answer = await execute_contract(*parts, transport)
    assert not answer["claims"] and not answer["citations"]
    assert answer["grounded"] is False
    assert answer["terminal_outcome"]["outcome"] == "verification_failed"
    assert service._runner._verified_checkpoint is None
