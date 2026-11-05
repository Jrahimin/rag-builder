"""Approved proof and independent personal inputs survive real publication boundaries."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.modules.conversations.services.evidence_repair_service import (
    _SearchPlan,
    repair_knowledge_evidence,
)
from app.modules.conversations.terminal_outcome import missing_input_questions
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.errors import ProviderTimeoutError
from app.platform.providers.request_work import RequestWork, current_request_purpose
from tests.unit.modules.conversations.test_grounding_service import _saved_agm_passage
from tests.unit.modules.conversations.test_phase2_completion import (
    _bind_active_verdict_fixture,
    execute_contract,
    source,
)
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit
VALID_AGM = "The interval between successive AGMs must not exceed 15 months."
PERSONAL_QUERY = "Calculate my liability. I have not provided the assessment year or income amount."
QUESTIONS = ["Which assessment year applies?", "What is your taxable income amount?"]


def obligations(query=PERSONAL_QUERY, requirement="R2"):
    return [
        {
            "kind": kind,
            "question": question,
            "requirement_ids": [requirement],
            "request_quote": query,
        }
        for kind, question in zip(["period", "amount"], QUESTIONS, strict=True)
    ]


def completion(payload):
    return ChatCompletionResult(
        content=json.dumps(payload),
        provider="fixture",
        model="fixture",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )


async def mapped(assertion, *, heading=False, verdict="supported", quote=None):
    passage = quote or _saved_agm_passage()
    title = "Section৮১ - বার্ষিক সাধারণ সভা"
    evidence = source(title + "\n\n" + passage)
    evidence = replace(
        evidence,
        metadata={
            "reviewed_proof": [{"requirement_id": "R1", "quote": passage, "fulfillment": "full"}],
            **({"heading_path": [title]} if heading else {}),
        },
    )
    llm = AsyncMock()
    llm.supports_output_contract = True
    llm.generate.return_value = completion({"verdicts": [verdict]})
    _bind_active_verdict_fixture(llm)
    embedder = AsyncMock()
    result = await GroundingService(
        ChatConfig(), entailment=ClaimEntailmentService(llm), embedder=embedder
    ).map_claims(
        assertion,
        [evidence],
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": assertion,
                "requirement_ids": ["R1"],
                "proof_ids": [str(evidence.chunk_id)],
            }
        ],
    )
    embedder.embed.assert_not_awaited()
    return result.claims[0], evidence, llm


@pytest.mark.parametrize("heading", [False, True])
async def test_approved_proof_not_locator_authenticates_typed_agm(heading):
    claim, evidence, llm = await mapped(VALID_AGM, heading=heading)
    assert claim["verification"] == "supported" and claim["grounded"] is True
    assert claim["assertion_id"] == "A1" and claim["requirement_ids"] == ["R1"]
    assert claim["evidence"][0]["chunk_id"] == str(evidence.chunk_id)
    proof = claim["evidence"][0]
    assert proof["evidence_span_hash"] == content_hash(_saved_agm_passage())
    assert proof["excerpt"] in _saved_agm_passage()
    assert "পনের মাসের অধিক হইবে না" in proof["excerpt"]
    sent = json.loads(llm.generate.call_args.args[0][1].content)[0]["proof"]
    assert sent[0]["quote"] == _saved_agm_passage()
    assert sent[0]["span_hash"] == proof["evidence_span_hash"]
    assert llm.generate.await_count == 1


@pytest.mark.parametrize("heading", [False, True])
@pytest.mark.parametrize("mutation", ["duration", "polarity", "exception", "scope"])
async def test_approved_proof_keeps_quantity_polarity_exception_and_semantic_guards(
    heading, mutation
):
    assertions = {
        "duration": VALID_AGM.replace("15", "16"),
        "polarity": "The interval between successive AGMs may exceed 15 months.",
        "exception": "The Registrar may extend the first AGM deadline.",
        "scope": VALID_AGM + " This applies to every foreign company.",
    }
    claim, _, _ = await mapped(
        assertions[mutation],
        heading=heading,
        verdict="unsupported" if mutation in {"polarity", "scope"} else "supported",
    )
    assert claim["verification"] != "supported" and claim["grounded"] is False


@pytest.mark.parametrize("heading", [False, True])
async def test_english_approved_clause_polarity_cannot_be_overridden_by_supported_verifier(heading):
    claim, _, _ = await mapped(
        "The interval may exceed 15 months.",
        heading=heading,
        quote="The interval must not exceed 15 months.",
    )
    assert claim["verification"] == "unsupported"
    assert claim["verification_method"] == "bounded_entailment"


@pytest.mark.parametrize("verdict", ["unverified", "unsupported"])
async def test_typed_semantic_failure_never_reports_intentionally_omitted_embedding(verdict):
    claim, _, _ = await mapped("The AGM rule applies to the requested period.", verdict=verdict)
    assert claim["verification"] == verdict and claim["grounded"] is False
    assert claim["verification_method"] == "source_entailment"
    assert claim["verification_reason"] == "unrelated_or_insufficient_evidence"
    assert claim["verifier_failures"][0]["dimension"] == "scope"


@pytest.mark.parametrize("heading", [False, True])
@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("termination", ["complete", "timeout"])
async def test_captured_bound_agm_survives_publication_checkpoint_and_saved_projection(
    heading, transport, termination
):
    service, provider, conversation, repository, question = journey()
    metadata = deepcopy(provider.source.metadata)
    if not heading:
        metadata.pop("heading_path", None)
    provider.source = replace(provider.source, metadata=metadata)
    service._retrieval.retrieve.return_value = replace(
        service._retrieval.retrieve.return_value, chunks=[provider.source]
    )
    original_review = provider.review

    def review():
        requirements, coverage = original_review()
        coverage["checks"][1]["answerable_scope"] = VALID_AGM
        return requirements, coverage

    provider.review = review
    if termination == "timeout":
        service._chat_config = service._runner._chat_config = ChatConfig(
            execution_policy="bounded_v2", bounded_recovery_enabled=True
        )
        original_verify = service._runner._verify_claims

        async def timeout(*args, **kwargs):
            await original_verify(*args, **kwargs)
            raise TimeoutError("controlled post-verification timeout")

        service._runner._verify_claims = timeout
    checkpoints = []
    original_checkpoint = service._runner._checkpoint_claims

    def checkpoint(prepared, claims):
        before = deepcopy(claims)
        original_checkpoint(prepared, claims)
        assert claims == before
        checkpoints.append(service._runner._verified_checkpoint.restore(prepared.intent.scope))

    service._runner._checkpoint_claims = checkpoint
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["grounded"] is True
    assert VALID_AGM in answer["content"]
    assert {r for c in answer["claims"] for r in c["requirement_ids"]} == {"R1", "R2"}
    assert answer["citations"][0]["chunk_id"] == str(provider.source.chunk_id)
    for saved in checkpoints:
        assert VALID_AGM in saved.content
        assert all(
            c["verification"] == "supported" and c["fulfilled_proof_requirements"]
            for c in saved.claims
        )
    assert checkpoints
    projection = MessageResponse.from_message(repository.add.call_args.args[0]).model_dump(
        mode="json"
    )
    for field in ["content", "claims", "citations", "terminal_outcome"]:
        assert projection[field] == answer[field]


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        {
            "complete": False,
            "missing": ["Governing law"],
            "missing_inputs": ["Unreviewed source gap"],
            "checks": [],
        },
    ],
)
@pytest.mark.parametrize("failure", ["coverage_unavailable", "timeout"])
async def test_initial_inputs_survive_unavailable_coverage_and_timeout(coverage, failure):
    plan = {
        "queries": [{"query": "governing rule", "requirement_ids": ["R2"]}],
        "requirements": [
            {
                "requirement_id": "R2",
                "description": "Calculate personal liability",
                "task_kind": "calculation",
            }
        ],
        "coverage": coverage,
        "missing_user_inputs": obligations(),
    }
    llm = AsyncMock()
    llm.generate.side_effect = [
        completion(plan),
        ProviderTimeoutError(
            "controlled review timeout",
            provider_name="fixture",
            context={"reason": "recovery_deadline_exceeded"},
        )
        if failure == "timeout"
        else completion({"complete": False, "missing": ["Governing law"], "checks": []}),
    ]
    retrieval = AsyncMock()
    retrieval.retrieve.return_value = ContextRetrievalResult(
        [], {"index_build_id": "build", "source_metadata_generation": 1}
    )
    work = RequestWork(__import__("uuid").uuid4())
    with work.attached():
        result = await repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(query=PERSONAL_QUERY),
            initial=ContextRetrievalResult(
                [], {"index_build_id": "build", "source_metadata_generation": 1}
            ),
            selected=[],
            retrieval=retrieval,
            llm=llm,
            grounding=GroundingService(ChatConfig()),
            chat_config=ChatConfig(),
            retrieval_config=RetrievalConfig(),
            max_output_tokens=4096,
            max_initial_queries=1,
            max_followup_queries=0,
            max_followup_rounds=0,
        )
    assert result.missing_inputs == tuple(QUESTIONS)
    assessment = result.diagnostics["initial_assessment"]
    assert assessment["missing_user_inputs"] == QUESTIONS
    assert assessment["missing_source_evidence"] == ["Calculate personal liability"]
    assert assessment["evidence_bindings"] == [] and assessment["coverage_validated"] is False
    assert work.evidence_snapshot["missing_user_inputs"] == QUESTIONS
    assert result.decision is None and not result.selected
    if failure == "timeout":
        assert result.failure is not None
        assert result.failure.context["reason"] == "recovery_deadline_exceeded"
    assert "Unreviewed source gap" not in result.missing_inputs


@pytest.mark.parametrize(
    "fault",
    ["foreign_requirement", "rule_lookup", "source_gap_kind", "foreign_quote", "empty_question"],
)
async def test_personal_input_obligations_reject_unbound_or_source_gap_classification(fault):
    requirements = [
        {"requirement_id": "R2", "description": "Calculate liability", "task_kind": "calculation"}
    ]
    items = obligations()
    if fault == "foreign_requirement":
        items[0]["requirement_ids"] = ["unknown"]
    elif fault == "rule_lookup":
        requirements[0]["task_kind"] = "rule_lookup"
    elif fault == "source_gap_kind":
        items[0]["kind"] = "source_rule"
    elif fault == "empty_question":
        items[0]["question"] = ""
    else:
        items[0]["request_quote"] = "Different request"
    payload = {"queries": [], "requirements": requirements, "missing_user_inputs": items}
    if fault != "foreign_quote":
        with pytest.raises(ValidationError):
            _SearchPlan.model_validate(payload)
        return
    llm = AsyncMock()
    llm.generate.return_value = completion(payload)
    result = await repair_knowledge_evidence(
        inputs=EffectiveRetrievalInputs(query=PERSONAL_QUERY),
        initial=ContextRetrievalResult(
            [], {"index_build_id": "build", "source_metadata_generation": 1}
        ),
        selected=[],
        retrieval=AsyncMock(),
        llm=llm,
        grounding=GroundingService(ChatConfig()),
        chat_config=ChatConfig(),
        retrieval_config=RetrievalConfig(),
        max_output_tokens=4096,
    )
    assert result.missing_inputs == (QUESTIONS[1],)
    assert result.diagnostics["initial_assessment"]["missing_user_inputs"] == [QUESTIONS[1]]


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("termination", ["complete", "timeout"])
async def test_independent_verified_general_rule_and_personal_question_share_projection(
    transport, termination
):
    service, provider, conversation, repository, _ = journey()
    question = (
        "Under Companies Act 1994, explain the first AGM deadline and maximum interval "
        "between subsequent AGMs, and decide if my company's first AGM is late. "
        "I have not supplied incorporation date."
    )
    asked = "What is your company's incorporation date?"
    original_generate = provider.generate

    async def generate(messages, **kwargs):
        result = await original_generate(messages, **kwargs)
        if current_request_purpose() == "recovery_planning":
            payload = json.loads(result.content)
            # Source coverage remains about the independently requested rules.
            payload["requirements"].append(
                {
                    "requirement_id": "R3",
                    "description": "Determine personal first AGM lateness",
                    "task_kind": "personal_eligibility",
                    "origin": "explicit_user_request",
                    "materiality": "adjacent_rule",
                }
            )
            payload["coverage"]["complete"] = False
            payload["coverage"]["missing"] = ["Personal lateness conclusion"]
            payload["coverage"]["checks"].append(
                {
                    "requirement_id": "R3",
                    "supported": False,
                    "fulfillment": "none",
                    "evidence": [],
                }
            )
            payload["coverage"]["partial_answer"] = {
                "scope": "Explain independently established AGM deadlines",
                "requirement_ids": ["R1", "R2"],
                "exclusions": ["Personal lateness conclusion"],
            }
            payload["missing_user_inputs"] = [
                {
                    "kind": "event_date",
                    "question": asked,
                    "requirement_ids": ["R3"],
                    "request_quote": question,
                }
            ]
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    if termination == "timeout":
        service._chat_config = service._runner._chat_config = ChatConfig(
            execution_policy="bounded_v2", bounded_recovery_enabled=True
        )
        original_verify = service._runner._verify_claims

        async def timeout(*args, **kwargs):
            await original_verify(*args, **kwargs)
            raise TimeoutError("controlled after independent rule verification")

        service._runner._verify_claims = timeout
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["grounded"] is True and len(answer["claims"]) == 2
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert asked in answer["content"]
    assert answer["content"].count(asked) == 1
    assert all(asked not in c["text"] for c in answer["claims"])
    assert len(answer["citations"]) == 1
    assert any(
        n["kind"] == "missing_scenario_input" and n["source"]["missing_inputs"] == [asked]
        for n in answer["notices"]
    )
    projection = MessageResponse.from_message(repository.add.call_args.args[0]).model_dump(
        mode="json"
    )
    for field in ["content", "claims", "citations", "terminal_outcome", "notices"]:
        assert projection[field] == answer[field]
    if termination == "timeout":
        assert answer["terminal_outcome"]["outcome"] == "partial"
    else:
        assert answer["terminal_outcome"]["outcome"] == "needs_input"


@pytest.mark.parametrize("language", ["en", "bn"])
def test_outer_deadline_uses_early_input_snapshot_without_factual_publication(language):
    service, _, _, _, _ = journey()
    service._work.evidence_snapshot.update({"missing_user_inputs": QUESTIONS})
    request = MessageSendRequest(
        content=PERSONAL_QUERY
        if language == "en"
        else "আমার আয়কর হিসাব করুন। করবর্ষ ও আয়ের পরিমাণ দেইনি।"
    )
    result = service._runner.deadline_result(
        request, reason="request_deadline_exceeded", failure_phase="coverage"
    )
    assert result.finalization.terminal.outcome == "timed_out"
    assert all(q in result.content for q in QUESTIONS)
    assert result.claims == [] and result.citations == [] and result.grounded is False
    assert result.metadata["notices"][-1]["kind"] == "missing_scenario_input"
    assert missing_input_questions(result.metadata) == QUESTIONS


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
async def test_missing_personal_inputs_survive_real_no_proof_recovery_timeout(transport):
    service, provider, conversation, repository, _ = journey()
    empty = replace(service._retrieval.retrieve.return_value, chunks=[])
    service._retrieval.retrieve.side_effect = [empty, TimeoutError("controlled recovery deadline")]
    plans = []

    async def generate(messages, **kwargs):
        if current_request_purpose() == "coverage_review":
            raise ProviderTimeoutError(
                "controlled recovery review deadline",
                provider_name="fixture",
                context={"reason": "recovery_deadline_exceeded"},
            )
        assert current_request_purpose() == "recovery_planning"
        plans.append(messages[0].content)
        return completion(
            {
                "queries": [{"query": "governing liability rule", "requirement_ids": ["R2"]}],
                "requirements": [
                    {
                        "requirement_id": "R2",
                        "description": "Calculate personal liability",
                        "task_kind": "calculation",
                    }
                ],
                "coverage": {
                    "complete": False,
                    "missing": ["Governing rule"],
                    "missing_inputs": ["Unreviewed gap"],
                    "checks": [],
                },
                "missing_user_inputs": obligations(),
            }
        )

    provider.generate = generate
    answer = await execute_contract(
        service, provider, conversation, repository, PERSONAL_QUERY, transport
    )
    assert plans and "missing_user_inputs separately" in plans[0]
    assert answer["terminal_outcome"]["outcome"] == "timed_out"
    assert all(q in answer["content"] for q in QUESTIONS)
    assert "2026" not in answer["content"] and "400,000" not in answer["content"]
    assert answer["claims"] == [] and answer["citations"] == [] and answer["grounded"] is False
    projection = MessageResponse.from_message(repository.add.call_args.args[0]).model_dump(
        mode="json"
    )
    for field in ["content", "claims", "citations", "notices", "terminal_outcome"]:
        assert projection[field] == answer[field]


async def test_cancellation_during_recovery_keeps_early_validated_input_obligations():
    plan = {
        "queries": [{"query": "governing rule", "requirement_ids": ["R2"]}],
        "requirements": [
            {
                "requirement_id": "R2",
                "description": "Calculate personal liability",
                "task_kind": "calculation",
            }
        ],
        "missing_user_inputs": obligations(),
    }
    llm = AsyncMock()
    llm.generate.return_value = completion(plan)
    retrieval = AsyncMock()
    retrieval.retrieve.side_effect = asyncio.CancelledError()
    work = RequestWork(__import__("uuid").uuid4())
    with work.attached(), pytest.raises(asyncio.CancelledError):
        await repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(query=PERSONAL_QUERY),
            initial=ContextRetrievalResult(
                [], {"index_build_id": "build", "source_metadata_generation": 1}
            ),
            selected=[],
            retrieval=retrieval,
            llm=llm,
            grounding=GroundingService(ChatConfig()),
            chat_config=ChatConfig(),
            retrieval_config=RetrievalConfig(),
            max_output_tokens=4096,
            max_initial_queries=1,
            max_followup_queries=0,
            max_followup_rounds=0,
        )
    assert work.evidence_snapshot["missing_user_inputs"] == QUESTIONS
    assert len(work.evidence_snapshot["input_obligations"]) == 2
