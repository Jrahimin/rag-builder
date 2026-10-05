"""Captured-proof regressions through preparation, providers, verification and persistence."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig, LLMBackend, LLMConfig, RetrievalConfig
from app.models.conversation import Conversation
from app.models.message_diagnostic import MessageDiagnostic
from app.modules.conversations.answer_draft import AnswerDraft
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.prompts.authoritative_compatibility import (
    AUTHORITATIVE_COVERAGE_PROMPT,
)
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.chat_service import ChatService
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import _validated_completion
from app.modules.conversations.services.recovery_schedule import RecoverySchedule
from app.modules.conversations.turn_resolution import normalize_request_scope
from app.platform.providers.capabilities import endpoint_identity
from app.platform.providers.contracts.llm import (
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.errors import ProviderTimeoutError
from app.platform.providers.implementations.gemini_chat import GeminiChatProvider
from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)
from app.platform.providers.request_work import ObservedLLM, RequestWork, current_request_purpose

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[3] / "fixtures/evaluation/post_qa_production_contract_v1.json"


def captured_source(identity):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = next(item for item in data["rows"] if item["id"] == identity)
    return ContextChunk(
        chunk_id=uuid.UUID(row["id"]),
        document_id=uuid.UUID(row["document_id"]),
        chunk_index=row["chunk_index"],
        content=row["content"],
        score=1.0,
        semantic_score=1.0,
        filename=row["filename"],
        chunk_hash=row["content_sha256"],
        metadata=row["metadata"],
        page_number=row["page_number"],
        char_start=row["char_start"],
        char_end=row["char_end"],
    )


class CapturedProvider(OpenAICompatibleChatProvider):
    """Controlled protocol outputs against captured immutable source spans."""

    def __init__(self, source, kind="agm", failure=None, combined=False):
        super().__init__(
            provider_name="openai",
            api_key="fixture",
            base_url="https://example.test",
            model="gpt-6-luna",
            provider_version="fixture",
            request_timeout_seconds=1,
            schema_capabilities=[
                {
                    "provider": "openai",
                    "model": "gpt-6-luna",
                    "endpoint_hash": endpoint_identity("https://example.test"),
                    "schema_mode": "json_object",
                    "reviewer": "controlled-wire-fixture",
                    "evidence_hash": "a" * 64,
                    "capability_revision": "fixture.json-object.v1",
                }
            ],
        )
        self.source = source
        self.kind = kind
        self.failure = failure
        self.combined = combined
        self.bodies = []
        self.purposes = []

    def review(self):
        if self.kind == "agm":
            descriptions = [
                "First AGM deadline after incorporation",
                "Maximum interval between subsequent AGMs",
            ]
            scopes = [
                "The first AGM may be held within 18 months from incorporation.",
                "The interval between one AGM and the next must not exceed 15 months.",
            ]
            ranges = [(7, 7), (5, 5)]
        else:
            descriptions = [
                "Applicable AY 2026-27 ordinary individual schedule",
                "Tax-free threshold for an ordinary individual in AY 2026-27",
            ]
            scopes = [
                "The schedule applies to AY 2026-27 ordinary individuals.",
                "The first 400,000 taka of total income is taxed at zero.",
            ]
            ranges = [(1, 7), (1, 14) if self.combined else (8, 14)]
        requirements = [
            {
                "requirement_id": "R1",
                "description": descriptions[0],
                "origin": "explicit_user_request",
                "materiality": "governing_applicability" if self.kind != "agm" else "central_rule",
            },
            {
                "requirement_id": "R2",
                "description": descriptions[1],
                "origin": "explicit_user_request",
                "materiality": "central_rule",
                "depends_on": ["R1"] if self.kind != "agm" else [],
            },
        ]
        checks = [
            {
                "requirement_id": identity,
                "description": description,
                "supported": True,
                "fulfillment": "full",
                "answerable_scope": scope,
                "evidence": [
                    {"chunk_id": str(self.source.chunk_id), "start_line": start, "end_line": end}
                ],
            }
            for identity, description, scope, (start, end) in zip(
                ("R1", "R2"), descriptions, scopes, ranges, strict=True
            )
        ]
        return requirements, {"complete": True, "missing": [], "checks": checks}

    def answer(self):
        _requirements, coverage = self.review()
        segments = [
            {
                "text": item["answerable_scope"],
                "requirement_ids": [item["requirement_id"]],
                "proof_ids": [str(self.source.chunk_id)],
            }
            for item in coverage["checks"]
        ]
        if self.failure == "foreign":
            segments[0]["proof_ids"] = [str(uuid.uuid4())]
        if self.failure == "extra":
            segments.append(
                {
                    "text": "Every company must pay a lunar registration fee.",
                    "requirement_ids": ["R1"],
                    "proof_ids": [str(self.source.chunk_id)],
                }
            )
        if self.failure == "wrong_period":
            segments[1]["text"] = "The first 400,000 taka of income is tax-free in AY 2025-26."
        if self.failure == "prose":
            return "Plain unstructured AGM answer."
        if self.failure == "array":
            return json.dumps(segments)
        return json.dumps({"version": "answer.draft.v1", "segments": segments})

    async def generate(self, messages, *, temperature=None, max_tokens, output_contract=None):
        purpose = current_request_purpose()
        self.purposes.append(purpose)
        body = self._body(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            output_contract=output_contract,
        )
        self.bodies.append(body)
        if purpose in {"recovery_planning", "structured_response_retry"}:
            requirements, coverage = self.review()
            content = json.dumps(
                {"requirements": requirements, "queries": [], "coverage": coverage}
            )
        elif purpose == "coverage_review":
            content = json.dumps(self.review()[1])
        elif purpose == "claim_verification":
            assertions = json.loads(messages[1].content)
            if self.failure == "verifier_schema":
                content = '{"verdicts": {"wrong": "shape"}}'
            else:
                allowed = [item["answerable_scope"] for item in self.review()[1]["checks"]]
                content = json.dumps(
                    {
                        "verdicts": [
                            "supported"
                            if any(item["assertion"].strip() == text for text in allowed)
                            else "unsupported"
                            for item in assertions
                        ]
                    }
                )
        else:
            assert "Fixed citation index" not in messages[0].content
            assert "Final answer check:" not in messages[0].content
            assert '"segments"' in messages[0].content
            assert body.get("response_format") == {"type": "json_object"}
            content = self.answer()
        return ChatCompletionResult(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
            finish_reason="stop",
            usage=ChatUsage(10, 5),
            provider_version="fixture",
        )

    async def stream(self, messages, *, temperature=None, max_tokens, output_contract=None):
        result = await self.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            output_contract=output_contract,
        )
        yield ChatCompletionChunk(delta=result.content)
        yield ChatCompletionChunk(delta="", finish_reason="stop", usage=result.usage)


def journey(kind="agm", failure=None, combined=False):
    source = captured_source(
        "4d0dd2f9-6902-495d-9c08-6dbdb536df1a"
        if kind == "agm"
        else "5aa3a215-d3da-4242-b16f-74df447729c4"
    )
    provider = CapturedProvider(source, kind=kind, failure=failure, combined=combined)
    conversation = Conversation(
        id=uuid.uuid4(),
        project_id=uuid.UUID(source.metadata["project_id"]),
        title=None,
        provider="openai",
        model="gpt-6-luna",
        temperature=None,
        system_prompt_version="v24",
        is_active=True,
        deleted_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=True)

    async def refresh(entity):
        if entity.id is None:
            entity.id = uuid.uuid4()
        if entity.created_at is None:
            entity.created_at = datetime.now(UTC)
        if entity.updated_at is None:
            entity.updated_at = datetime.now(UTC)
        if hasattr(entity, "message_metadata") and entity.message_metadata is None:
            entity.message_metadata = {}

    session.refresh.side_effect = refresh
    conversations = AsyncMock()
    conversations.get_by_id.return_value = conversation
    messages = AsyncMock()
    messages.add = MagicMock()
    messages.list_recent_for_conversation.return_value = []
    retrieval = SimpleNamespace(
        retrieve=AsyncMock(
            return_value=ContextRetrievalResult(
                chunks=[source],
                diagnostics={
                    "index_build_id": source.metadata["index_build_id"],
                    "source_metadata_generation": source.metadata["source_metadata_generation"],
                },
            )
        )
    )
    service = ChatService(
        session,
        conversation.project_id,
        conversations,
        messages,
        retrieval,
        ChatConfig(bounded_recovery_enabled=True),
        RetrievalConfig(),
        LLMConfig(backend=LLMBackend.OPENAI, model="gpt-6-luna", max_tokens=4096, temperature=None),
        resolve_llm=lambda _: provider,
    )
    question = (
        "Under Companies Act 1994, what is the first AGM deadline and maximum interval "
        "between subsequent AGMs?"
        if kind == "agm"
        else "What is the tax-free threshold for an ordinary individual for AY 2026-27?"
    )
    return service, provider, conversation, messages, question


@pytest.mark.parametrize("repetition", range(3))
async def test_captured_agm_composed_prompt_provider_parser_verifier_persistence(repetition):
    service, provider, conversation, repository, question = journey()
    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))
    answer = turn.assistant_message
    assert answer.terminal_outcome.outcome == "answered"
    assert answer.grounded is True and len(answer.claims) >= 2
    assert "18 months" in answer.content and "15 months" in answer.content
    assert answer.citations and all(item.verification == "supported" for item in answer.claims)
    assert "আঠারো" in answer.citations[0].excerpt and "পনের" in answer.citations[0].excerpt
    assert any(
        "পনের" in (evidence.excerpt or "")
        for claim in answer.claims
        if "R2" in claim.requirement_ids
        for evidence in claim.evidence
    )
    assert "recovery_planning" in provider.purposes
    assert "answer_generation" in provider.purposes and "claim_verification" in provider.purposes
    saved = repository.add.call_args.args[0]
    assert MessageResponse.from_message(saved).terminal_outcome == answer.terminal_outcome
    assert answer.metadata["lifecycle"]["persistence_completed"] is True


@pytest.mark.parametrize("combined", [False, True])
async def test_captured_threshold_split_and_combined_repair_paths_are_equivalent(combined):
    service, _, conversation, _, question = journey("threshold", combined=combined)
    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))
    answer = turn.assistant_message
    assert answer.terminal_outcome.outcome == "answered"
    assert answer.grounded is True and "400,000" in answer.content
    checks = answer.metadata["knowledge_repair"]["coverage"]["checks"]
    assert all(item["fulfillment"] == "full" for item in checks)
    assert len(checks[1]["source_ranges"]) >= (1 if combined else 2)


@pytest.mark.parametrize("failure", ["prose", "array", "foreign", "verifier_schema"])
async def test_generation_protocol_failures_are_distinct_and_never_publish_failed_drafts(failure):
    service, provider, conversation, messages, question = journey(failure=failure)
    diagnostics = SimpleNamespace(expire_payloads=AsyncMock(), add=MagicMock())
    if failure == "verifier_schema":
        # Full payloads are opt-in and stored separately from public messages.
        # Capture the MessageDiagnostic produced by the actual production writer.
        service._diagnostic_repository = diagnostics
        service._diagnostic_capture = True

        async def assign_message_identity():
            message = messages.add.call_args.args[0]
            if message.id is None:
                message.id = uuid.uuid4()

        messages.flush.side_effect = assign_message_identity
    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))
    answer = turn.assistant_message
    assert answer.terminal_outcome.outcome == "verification_failed"
    assert not answer.claims and not answer.citations
    assert "narrower" not in answer.content
    assert answer.metadata["evidence_funnel"]["outcome"] == "verification_failed"
    assert answer.terminal_outcome.failure_stage == (
        "claim_verification" if failure == "verifier_schema" else "draft_schema"
    )
    assert provider.purposes.count("answer_shape_correction") <= 1
    if failure == "verifier_schema":
        assert "claim_verification" in provider.purposes
        assert service._work.counts["semantic_repairs"] == 0
        assert "verifier_schema_invalid" in answer.metadata["rejected_draft"]["reasons"]
        diagnostics.expire_payloads.assert_awaited_once()
        diagnostics.add.assert_called_once()
        record = diagnostics.add.call_args.args[0]
        assert isinstance(record, MessageDiagnostic)
        assert record.project_id == conversation.project_id and record.message_id == answer.id
        assert record.payload is not None and record.expires_at is not None
        rejected = record.payload["rejected_attempts"]
        assert rejected and all(
            row["id"] and row["reason"] == "verifier_schema_invalid" for row in rejected
        )
        assert "operator_diagnostic" not in answer.metadata
        assert "operator_diagnostic" not in messages.add.call_args.args[0].message_metadata


async def test_extra_assertion_does_not_reuse_approved_requirement_as_proof():
    service, _, conversation, _, question = journey(failure="extra")
    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))
    answer = turn.assistant_message
    assert "lunar registration fee" not in answer.model_dump_json()
    assert all(item.verification == "supported" for item in answer.claims)


async def test_regular_stream_done_and_persisted_get_share_terminal_contract():
    regular, _, conversation, _, question = journey()
    expected = (
        await regular.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    streaming, _, conversation, repository, question = journey()
    events = [
        item
        async for item in streaming.stream_message(
            conversation.id, MessageSendRequest(content=question)
        )
    ]
    done = next(item for item in events if isinstance(item, dict) and item.get("event") == "done")
    saved = MessageResponse.from_message(repository.add.call_args.args[0])
    assert done["terminal_outcome"] == saved.terminal_outcome.model_dump(mode="json")
    assert saved.terminal_outcome == expected.terminal_outcome
    assert saved.content == expected.content
    assert done["claims"] == [item.model_dump(mode="json") for item in saved.claims]
    assert done["lifecycle"]["persistence_completed"] is True


async def test_deterministic_nonfactual_failure_skips_semantic_review():
    service, provider, conversation, _, question = journey()
    service._retrieval = SimpleNamespace(
        retrieve=AsyncMock(return_value=ContextRetrievalResult(chunks=[], diagnostics={}))
    )
    # Empty evidence provider planning cannot complete reviewed proof.
    provider.review = lambda: ([], {"complete": False, "missing": ["source rule"], "checks": []})
    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))
    assert turn.assistant_message.terminal_outcome.outcome == "insufficient_evidence"
    assert "claim_verification" not in provider.purposes


async def test_real_coverage_prompt_and_body_use_canonical_condition_array():
    source = captured_source("46c4724c-4afa-4149-b861-b2085efaea39")
    provider = CapturedProvider(source)
    facets = [
        {
            "who": "tenant",
            "action": "supply rental agreement",
            "when": "",
            "condition": "rented premises",
            "evidence_indexes": [0],
        },
        {
            "who": "factory operator",
            "action": "supply fire clearance",
            "when": "new application",
            "condition": "factory",
            "evidence_indexes": [0],
        },
    ]
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "requirement_id": "R1",
                "supported": True,
                "fulfillment": "full",
                "description": "Conditional tenant/factory checklist",
                "condition_facets": facets,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
            }
        ],
    }
    provider.review = lambda: ([], coverage)
    work = RequestWork(uuid.uuid4())
    llm = ObservedLLM(provider, work)
    result = await _validated_completion(
        llm,
        [
            ChatMessage(role=ChatRole.SYSTEM, content=AUTHORITATIVE_COVERAGE_PROMPT),
            ChatMessage(role=ChatRole.USER, content="Review captured tenant/factory passage"),
        ],
        schema=CoverageVerdict,
        temperature=None,
        max_tokens=4096,
        call_purpose="coverage_review",
        proof_context=[source],
        expected_requirement_ids={"R1"},
    )
    parsed = CoverageVerdict.model_validate_json(result.content)
    assert len(parsed.checks[0].condition_facets) == 2
    assert provider.bodies[0]["response_format"] == {"type": "json_object"}
    assert "condition_facets is an ARRAY" in AUTHORITATIVE_COVERAGE_PROMPT
    coverage["checks"][0]["condition_facets"] = facets[0]
    with pytest.raises(ValidationError):
        await _validated_completion(
            llm,
            [
                ChatMessage(role=ChatRole.SYSTEM, content=AUTHORITATIVE_COVERAGE_PROMPT),
                ChatMessage(role=ChatRole.USER, content="Review same source"),
            ],
            schema=CoverageVerdict,
            temperature=None,
            max_tokens=4096,
            call_purpose="coverage_review",
            proof_context=[source],
        )
    assert work.counts["malformed_correction_exchanges"] <= 1


def test_task_stipulation_and_eligibility_are_distinct():
    lookup = normalize_request_scope("What must a specified person deduct on office rent?")
    personal = normalize_request_scope("Do I qualify as a specified person for office rent?")
    assert lookup.task_kind == "lookup" and lookup.stipulated_facts
    assert personal.task_kind == "eligibility" and not personal.stipulated_facts


def test_one_deadline_preserves_normal_finalization_and_action_validation():
    work = RequestWork(uuid.uuid4())
    work.deadline = work.started + 50
    assert work.request_deadline - work.recovery_deadline == 30
    schedule = RecoverySchedule(time.monotonic() + 5)
    assert not schedule.admit(
        "search", requirement_ids=["R1"], fingerprint="same", expected_change="new central evidence"
    )
    assert schedule.actions[-1]["terminal_result"] == "exhausted_budget"


def test_changed_evidence_identity_can_admit_same_query_and_cancelled_is_not_complete():
    schedule = RecoverySchedule(time.monotonic() + 20)
    for fingerprint in ["scope-a-evidence-a", "scope-a-evidence-b"]:
        assert schedule.admit(
            "search",
            requirement_ids=["R1"],
            fingerprint=fingerprint,
            expected_change="changed evidence",
        )
    schedule.close(cancelled=True)
    assert all(action["terminal_result"] == "cancelled" for action in schedule.actions)


async def test_wrong_period_claim_cannot_reuse_current_schedule_proof():
    service, _, conversation, _, question = journey("threshold", failure="wrong_period")
    answer = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    assert "AY 2025-26" not in answer.content
    assert all(item.verification == "supported" for item in answer.claims)


async def test_expired_provider_phase_preserves_terminal_persistence_and_language():
    service, provider, conversation, repository, _ = journey()
    work = service._work
    # Recovery already exhausted, with normal finalization still available.
    work.deadline = time.perf_counter() + 9
    with work.stage("answer_generation"), pytest.raises(ProviderTimeoutError):
        await ObservedLLM(provider, work).generate(
            [ChatMessage(role=ChatRole.USER, content="No remaining generation budget")],
            max_tokens=32,
        )
    _, _, assistant = await service._deadline_terminal(
        conversation.id, MessageSendRequest(content="বাংলায় উত্তর দিন: প্রথম সভার সময়সীমা কী?")
    )
    response = MessageResponse.from_message(assistant)
    assert response.terminal_outcome.outcome == "timed_out"
    assert "সময়সীমার" in response.content
    assert not response.claims and not response.citations
    assert "claim_verification" not in provider.purposes
    assert repository.add.call_args.args[0].id == assistant.id


def test_other_adapter_and_unknown_compatible_constrained_intent():
    contract = AnswerDraft.contract()
    message = [ChatMessage(role=ChatRole.USER, content="Answer from approved proof")]
    from app.platform.providers.capabilities import endpoint_identity

    gemini = GeminiChatProvider(
        api_key="fixture",
        base_url="https://example.test",
        model="gemini-2.5-flash",
        schema_capabilities=[
            {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "endpoint_hash": endpoint_identity("https://example.test"),
                "schema_mode": "json_schema",
                "reviewer": "offline-fixture",
                "evidence_hash": "a" * 64,
                "capability_revision": "fixture.v1",
            }
        ],
        provider_version="fixture",
        request_timeout_seconds=1,
    )
    body = gemini._request_body(message, max_tokens=64, output_contract=contract)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == contract.schema
    compatible = OpenAICompatibleChatProvider(
        provider_name="openai_compatible",
        api_key="fixture",
        base_url="https://example.test",
        model="unknown-model",
        provider_version="fixture",
        request_timeout_seconds=1,
    )
    body = compatible._body(message, max_tokens=64, stream=False, output_contract=contract)
    assert "response_format" not in body
    assert any("segments" in item["content"] for item in body["messages"])


async def test_legacy_provider_contract_propagates_through_observed_wrapper():
    class Legacy:
        supports_output_contract = False
        provider_name = "legacy"
        model_name = "legacy"
        provider_version = "fixture"

        async def generate(self, messages, *, temperature=None, max_tokens):
            self.messages = messages
            return ChatCompletionResult(
                content='{"version":"answer.draft.v1","segments":[{"text":"No fact.",'
                '"requirement_ids":[],"proof_ids":[]}]}',
                provider=self.provider_name,
                model=self.model_name,
                finish_reason="stop",
                usage=ChatUsage(1, 1),
                provider_version="fixture",
            )

    provider = Legacy()
    observed = ObservedLLM(provider, RequestWork(uuid.uuid4()))
    with observed.work.stage("answer_generation"):
        await observed.generate_structured(
            [ChatMessage(role=ChatRole.USER, content="No factual assertion")],
            output_contract=AnswerDraft.contract(),
            max_tokens=64,
        )
    assert provider.messages[0].role == ChatRole.SYSTEM
    assert "segments" in provider.messages[0].content
    assert provider.messages[-1].content == "No factual assertion"


async def test_rejected_assertions_are_absent_from_regular_get_and_all_sse_payloads():
    marker = "lunar registration fee"
    regular, _, conversation, repository, question = journey(failure="extra")
    result = await regular.send_message(conversation.id, MessageSendRequest(content=question))
    persisted = MessageResponse.from_message(repository.add.call_args.args[0])
    assert marker not in result.model_dump_json()
    assert marker not in persisted.model_dump_json()
    stream, _, conversation, repository, question = journey(failure="extra")
    events = [
        item
        async for item in stream.stream_message(
            conversation.id, MessageSendRequest(content=question)
        )
    ]
    assert marker not in json.dumps(events)
    assert (
        marker
        not in MessageResponse.from_message(repository.add.call_args.args[0]).model_dump_json()
    )


@pytest.mark.parametrize(
    "question",
    [
        "Explain whether I qualify as a specified person.",
        "Calculate withholding and determine whether I qualify as a specified person.",
        "Does my company qualify as a specified person?",
    ],
)
def test_explicit_eligibility_survives_mixed_task_and_named_category(question):
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _prepare_search_plan,
        _SearchPlan,
    )

    scope = normalize_request_scope(question)
    assert scope.eligibility_requested is True
    assert not scope.stipulated_facts
    requirement = EvidenceRequirement(
        requirement_id="eligibility",
        description="Determine eligibility",
        origin="explicit_user_request",
        task_kind="personal_eligibility",
        materiality="central_rule",
    )
    plan = _SearchPlan(requirements=[requirement], queries=[])
    assert _prepare_search_plan(plan, question)[0] == [requirement]


@pytest.mark.parametrize(
    "intervening",
    [
        "",
        "Table B: AY 2025-26 ordinary individual",
        "Table B: AY 2026-27 female individual",
        "Section 2: private company",
    ],
)
def test_same_chunk_dependency_requires_governing_selector_boundary(intervening):
    from dataclasses import replace

    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _guard_review_fulfillment,
    )

    source = captured_source("5aa3a215-d3da-4242-b16f-74df447729c4")
    heading = "Table A: AY 2026-27 ordinary individual\n"
    text = heading + (intervening + "\n" if intervening else "") + "First 400000 taka | 0%\n"
    source = replace(source, content=text)
    own_line = 3 if intervening else 2
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "fulfillment": "full",
                    "description": "Applicable AY 2026-27 ordinary schedule",
                    "evidence": [
                        {"chunk_id": str(source.chunk_id), "start_line": 1, "end_line": 1}
                    ],
                },
                {
                    "requirement_id": "R2",
                    "supported": True,
                    "fulfillment": "full",
                    "description": "AY 2026-27 ordinary threshold",
                    "evidence": [
                        {
                            "chunk_id": str(source.chunk_id),
                            "start_line": own_line,
                            "end_line": own_line,
                        }
                    ],
                },
            ],
        }
    )
    assert verdict.resolve_source_ranges([source])
    requirements = [
        EvidenceRequirement(
            requirement_id="R1", description="Applicable AY 2026-27 ordinary schedule"
        ),
        EvidenceRequirement(
            requirement_id="R2", description="AY 2026-27 ordinary threshold", depends_on=["R1"]
        ),
    ]
    guarded = _guard_review_fulfillment(verdict, requirements, [source], "AY 2026-27 threshold")
    assert guarded.complete is (not bool(intervening))
    assert (guarded.checks[1].fulfillment == "full") is (not bool(intervening))


async def test_late_planner_without_admitted_search_persists_recovery_timeout_not_500(monkeypatch):
    service, provider, conversation, _, question = journey("threshold")
    original = provider.generate
    from app.modules.conversations.services import evidence_repair_service, recovery_schedule

    real_clock = time.monotonic
    advance = [0.0]
    monkeypatch.setattr(evidence_repair_service, "monotonic", lambda: real_clock() + advance[0])
    monkeypatch.setattr(recovery_schedule, "monotonic", lambda: real_clock() + advance[0])

    async def late_plan(messages, **kwargs):
        if current_request_purpose() == "recovery_planning":
            advance[0] = 26.0
            requirements, _coverage = provider.review()
            return ChatCompletionResult(
                content=json.dumps(
                    {
                        "requirements": requirements,
                        "queries": [{"query": "threshold", "requirement_ids": ["R2"]}],
                    }
                ),
                provider=provider.provider_name,
                model=provider.model_name,
                finish_reason="stop",
                usage=ChatUsage(1, 1),
                provider_version="fixture",
            )
        return await original(messages, **kwargs)

    provider.generate = late_plan
    result = await service.send_message(conversation.id, MessageSendRequest(content=question))
    answer = result.assistant_message
    assert answer.terminal_outcome.outcome == "timed_out"
    assert answer.terminal_outcome.reason_code == "recovery_deadline_exceeded"
    assert answer.terminal_outcome.unresolved_requirement_ids
    assert not answer.claims and not answer.citations
    assert all(
        item["kind"] not in {"verification_failed", "unresolved_authority"}
        for item in answer.metadata["notices"]
    )


@pytest.mark.parametrize(
    "phase,stage",
    [
        ("planning", "coverage"),
        ("retrieval", "retrieval"),
        ("coverage_review", "coverage"),
        ("claim_verification", "claim_verification"),
    ],
)
def test_terminal_timeout_projection_has_one_cause_across_fields(phase, stage):
    from app.modules.conversations.terminal_outcome import terminal_outcome, terminal_projection

    outcome = terminal_outcome(
        reason="unresolved_authority",
        supported_claims=0,
        partial=False,
        missing_inputs=[],
        diagnostics={
            "knowledge_repair": {
                "phase": phase,
                "stop_reason": "exhausted_budget",
                "requirements": [{"requirement_id": "R1"}],
            }
        },
        coverage="incomplete",
    )
    assert outcome.failure_stage == stage and outcome.reason_code == "recovery_deadline_exceeded"
    content, finish, reason, notices = terminal_projection(
        outcome,
        language="bn",
        content="incorrect amendment claim",
        finish_reason="insufficient_evidence",
        legacy_reason="unresolved_authority",
        notices=[{"kind": "unresolved_authority"}],
    )
    assert finish == reason == "recovery_deadline_exceeded"
    assert "incorrect amendment" not in content
    assert len(notices) == 1 and notices[0]["kind"] == "recovery_timeout"
    assert outcome.unresolved_requirement_ids == ["R1"]


@pytest.mark.parametrize("mutation", ["source_revision_id", "index_build_id", "table_id"])
def test_conflicting_same_chunk_provenance_cannot_compose_dependencies(mutation):
    from dataclasses import replace

    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _guard_review_fulfillment,
    )

    source = captured_source("5aa3a215-d3da-4242-b16f-74df447729c4")
    changed = replace(source, metadata={**source.metadata, mutation: "foreign"})
    checks = [
        {
            "requirement_id": "R1",
            "supported": True,
            "fulfillment": "full",
            "evidence": [{"chunk_id": str(source.chunk_id), "start_line": 1, "end_line": 7}],
        },
        {
            "requirement_id": "R2",
            "supported": True,
            "fulfillment": "full",
            "evidence": [{"chunk_id": str(source.chunk_id), "start_line": 8, "end_line": 14}],
        },
    ]
    review = CoverageVerdict.model_validate({"complete": True, "missing": [], "checks": checks})
    assert review.resolve_source_ranges([source])
    requirements = [
        EvidenceRequirement(requirement_id="R1", description="Applicable AY 2026-27"),
        EvidenceRequirement(
            requirement_id="R2", description="Threshold AY 2026-27", depends_on=["R1"]
        ),
    ]
    guarded = _guard_review_fulfillment(review, requirements, [source, changed])
    assert guarded.complete is False
    assert not any(check.fulfillment == "full" for check in guarded.checks)


async def test_shape_correction_cannot_rebind_original_proof():
    service, provider, conversation, _, question = journey()
    original_answer = provider.answer

    def malformed_then_rebound():
        draft = json.loads(original_answer())
        if current_request_purpose() == "answer_shape_correction":
            draft["segments"][0]["requirement_ids"] = ["R2"]
            return json.dumps(draft)
        return json.dumps(draft["segments"])

    provider.answer = malformed_then_rebound
    answer = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    assert answer.terminal_outcome.outcome == "verification_failed"
    assert (
        answer.metadata["answer_draft"]["shape_correction"] == "rejected_assertion_or_proof_change"
    )
    assert not answer.claims and not answer.citations


async def test_provider_semaphore_timeout_never_sends_after_expiry_and_cancellation_is_distinct():
    class WaitingProvider:
        supports_output_contract = False
        provider_name = "legacy"
        model_name = "legacy"
        provider_version = "fixture"

        def __init__(self):
            self.semaphore = asyncio.Semaphore(0)
            self.sent = 0

        async def generate(self, messages, *, temperature=None, max_tokens):
            async with self.semaphore:
                self.sent += 1
                raise AssertionError("An expired semaphore waiter must not send")

    provider = WaitingProvider()
    work = RequestWork(uuid.uuid4())
    work.deadline = time.perf_counter() + 10.025
    with work.stage("answer_generation"), pytest.raises(ProviderTimeoutError):
        await ObservedLLM(provider, work).generate(
            [ChatMessage(role=ChatRole.USER, content="Wait")],
            max_tokens=16,
        )
    assert provider.sent == 0
    work = RequestWork(uuid.uuid4())
    observed = ObservedLLM(provider, work)
    with work.stage("answer_generation"):
        pending = asyncio.create_task(
            observed.generate(
                [ChatMessage(role=ChatRole.USER, content="Wait")],
                max_tokens=16,
            )
        )
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert provider.sent == 0
    assert work.calls[-1]["status"] == "cancelled"


async def test_expired_immediate_provider_is_never_called():
    service, provider, _, _, _ = journey()
    service._work.deadline = time.perf_counter() + 9
    with service._work.stage("answer_generation"), pytest.raises(ProviderTimeoutError):
        await ObservedLLM(provider, service._work).generate(
            [ChatMessage(role=ChatRole.USER, content="expired")],
            max_tokens=16,
        )
    assert provider.purposes == []


async def test_delayed_search_keeps_delta_reserve_and_records_cancelled_action(monkeypatch):
    service, provider, conversation, _, question = journey("threshold")
    real_generate = provider.generate

    async def planning_without_coverage(messages, **kwargs):
        if current_request_purpose() == "recovery_planning":
            requirements, _coverage = provider.review()
            return ChatCompletionResult(
                content=json.dumps(
                    {
                        "requirements": requirements,
                        "queries": [{"query": "threshold", "requirement_ids": ["R1", "R2"]}],
                    }
                ),
                provider=provider.provider_name,
                model=provider.model_name,
                finish_reason="stop",
                usage=ChatUsage(1, 1),
                provider_version="fixture",
            )
        return await real_generate(messages, **kwargs)

    provider.generate = planning_without_coverage
    initial = service._retrieval.retrieve
    called = [0]

    async def retrieval(**kwargs):
        called[0] += 1
        if called[0] > 1:
            await asyncio.sleep(0.05)
        return await initial(**kwargs)

    service._retrieval.retrieve = retrieval
    from app.modules.conversations.services.recovery_schedule import RecoverySchedule

    monkeypatch.setattr(
        RecoverySchedule, "search_deadline", property(lambda self: time.monotonic() + 0.015)
    )
    answer = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    assert answer.terminal_outcome.outcome == "timed_out"
    actions = answer.metadata["knowledge_repair"]["recovery_actions"]
    assert actions and actions[-1]["terminal_result"] == "cancelled"
    assert not answer.claims and not answer.citations


async def test_persisted_legacy_draft_is_sanitized_without_mutating_the_saved_row():
    service, _, conversation, repository, question = journey()
    await service.send_message(conversation.id, MessageSendRequest(content=question))
    saved = repository.add.call_args.args[0]
    saved.message_metadata["answer_draft"] = {
        "version": "answer.draft.v1",
        "status": "rendered",
        "candidate_count": 1,
        "segments": [
            {
                "text": "lunar registration fee",
                "proof_ids": ["foreign"],
                "requirement_ids": ["unapproved"],
            }
        ],
    }
    response = MessageResponse.from_message(saved)
    assert "lunar registration fee" not in response.model_dump_json()
    assert "segments" not in response.metadata["answer_draft"]
    assert saved.message_metadata["answer_draft"]["segments"][0]["text"] == "lunar registration fee"


@pytest.mark.parametrize("malformed", [None, True, "raw", [], {"shape_correction": []}])
def test_public_draft_diagnostics_rejects_malformed_payload(malformed):
    from app.modules.conversations.answer_draft import public_draft_diagnostics

    public = public_draft_diagnostics(malformed)
    assert public is None or "shape_correction" not in public


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "document_id",
        "source_revision_id",
        "index_build_id",
        "table_id",
        "year",
        "category",
    ],
)
def test_cross_chunk_governing_dependencies_require_matching_selected_scope(mutation):
    from dataclasses import replace

    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _guard_review_fulfillment,
    )

    source = captured_source("5aa3a215-d3da-4242-b16f-74df447729c4")
    identity = {**source.metadata, "table_id": "schedule-A"}
    heading = replace(
        source, content="Table A: AY 2026-27 ordinary individual\n", metadata=identity
    )
    metadata = dict(identity)
    if mutation in {"source_revision_id", "index_build_id", "table_id"}:
        metadata[mutation] = "foreign"
    row_text = (
        "AY 2025-26 first 400000 taka | 0%\n"
        if mutation == "year"
        else "Female individual first 400000 taka | 0%\n"
        if mutation == "category"
        else "First 400000 taka | 0%\n"
    )
    row = replace(
        heading,
        chunk_id=uuid.uuid4(),
        chunk_index=1,
        content=row_text,
        metadata=metadata,
        document_id=uuid.uuid4() if mutation == "document_id" else heading.document_id,
    )
    requirements = [
        EvidenceRequirement(
            requirement_id="R1", description="Applicable AY 2026-27 ordinary schedule"
        ),
        EvidenceRequirement(
            requirement_id="R2", description="AY 2026-27 ordinary threshold", depends_on=["R1"]
        ),
    ]
    review = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": requirement.requirement_id,
                    "supported": True,
                    "fulfillment": "full",
                    "evidence": [{"chunk_id": str(chunk.chunk_id), "start_line": 1, "end_line": 1}],
                }
                for requirement, chunk in zip(requirements, (heading, row), strict=True)
            ],
        }
    )
    assert review.resolve_source_ranges([heading, row])
    guarded = _guard_review_fulfillment(review, requirements, [heading, row])
    assert guarded.complete is (mutation is None)
    assert (guarded.checks[1].fulfillment == "full") is (mutation is None)


def test_shape_correction_timeout_is_timeout_instead_of_schema_failure():
    from app.modules.conversations.terminal_outcome import terminal_outcome

    outcome = terminal_outcome(
        reason="claim_verification_failed",
        supported_claims=0,
        partial=False,
        missing_inputs=[],
        diagnostics={
            "answer_draft": {
                "status": "failed_verification",
                "timeout_reason": "request_deadline_exceeded",
            }
        },
        coverage="complete",
    )
    assert outcome.outcome == "timed_out"
    assert outcome.reason_code == "request_deadline_exceeded"
    assert outcome.failure_stage == "draft_schema"


@pytest.mark.parametrize(
    "cause,stage",
    [
        ("recovery_deadline_exceeded", "retrieval"),
        ("provider_timeout", "claim_verification"),
    ],
)
async def test_outer_transport_timeout_preserves_actual_provider_phase(cause, stage):
    service, _, conversation, _, question = journey()
    failure = ProviderTimeoutError(
        "controlled timeout",
        provider_name="fixture",
        context={"reason": cause, "phase": stage},
    )
    _, _, saved = await service._deadline_terminal(
        conversation.id,
        MessageSendRequest(content=question),
        failure=failure,
    )
    response = MessageResponse.from_message(saved)
    assert response.terminal_outcome.outcome == "timed_out"
    assert response.terminal_outcome.reason_code == response.insufficient_evidence_reason == cause
    assert response.terminal_outcome.failure_stage == stage
    assert response.finish_reason == cause
    assert not response.claims and not response.citations
    assert all(
        notice.kind not in {"unresolved_authority", "verification_failed"}
        for notice in response.notices
    )
