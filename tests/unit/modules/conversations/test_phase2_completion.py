"""Phase 2 deterministic contracts against captured evidence and real turn paths."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import ChatConfig, Settings
from app.modules.conversations.answer_draft import AnswerDraft
from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import _validated_completion
from app.modules.conversations.services.message_execution_runner_service import (
    _has_incomplete_trailing_fragment,
    _render_structured_answer,
    _user_facing_gap_details,
)
from app.modules.conversations.services.recovery_schedule import RecoverySchedule
from app.modules.conversations.turn_resolution import (
    normalize_request_scope,
    requires_complex_execution_budget,
)
from app.modules.evaluation.ports import QualityHit
from app.platform.config.project_ai import ConfigRevisionRecord, resolve_project_ai_config
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.request_work import RequestWork
from tests.unit.modules.conversations.captured_fixture_helpers import (
    load_captured_fixture,
    recorded_repair_for_case,
)
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


def test_live_qa_rule_lookup_intent_and_publication_integrity_regressions():
    lookup = normalize_request_scope(
        "For AY 2026-27, explain the income-tax rebate formula. This is a rule lookup, "
        "not a personal calculation, so no personal investment amount is needed."
    )
    salary = normalize_request_scope(
        "Assume gross employment income BDT 2,400,000 and investment BDT 100,000 for "
        "assessment year 2026-27. Show taxable income, applicable rate bands, rebate "
        "and cap, and estimate income tax before TDS and surcharge."
    )
    assert lookup.task_kind == "lookup"
    assert not requires_complex_execution_budget(lookup)
    assert requires_complex_execution_budget(salary)
    assert _has_incomplete_trailing_fragment("... increased in subsequent y")
    assert not _has_incomplete_trailing_fragment("The proposal lists the 2027 threshold.")
    assert _user_facing_gap_details(
        ["R1: Current filing schedule is not included.", "unproven governing dependency R1"]
    ) == ["Current filing schedule is not included."]


def source(text: str, *, reviewed: bool = False) -> ContextChunk:
    identity = uuid.uuid4()
    return ContextChunk(
        chunk_id=identity,
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=text,
        score=1.0,
        semantic_score=1.0,
        filename="controlled-proof.md",
        chunk_hash=hashlib.sha256(text.encode()).hexdigest(),
        metadata={
            "reviewed_proof": [{"requirement_id": "R1", "quote": text, "fulfillment": "full"}]
        }
        if reviewed
        else {},
    )


DIMENSIONS = [
    (
        "subject_category",
        "A public employee may claim the allowance.",
        "A private employee may claim the allowance.",
    ),
    ("period", "The allowance applies in AY 2026-27.", "The allowance applies in AY 2025-26."),
    (
        "condition",
        "A tenant with rented premises must supply the rental agreement.",
        "Every owner must supply the rental agreement.",
    ),
    (
        "certificate",
        "A deduction certificate establishes the source tax credit.",
        "An investment receipt establishes the source tax credit.",
    ),
    (
        "quantity_role",
        "The maximum penalty is Tk 100 per day.",
        "The filing fee is Tk 100 per day.",
    ),
    (
        "legal_effect",
        "The speech proposes a Tk 400,000 threshold.",
        "The enacted law establishes a Tk 400,000 threshold.",
    ),
]


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("dimension,proof,assertion", DIMENSIONS)
async def test_wrong_dimension_requires_semantic_verification(
    reviewed, dimension, proof, assertion
):
    evidence = source(proof, reviewed=reviewed)
    llm = AsyncMock()
    llm.supports_output_contract = True
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(
            {
                "verdicts": [
                    {
                        "status": "unsupported",
                        "failed_dimensions": [dimension],
                        "evidence_binding": "provider text must not leak",
                    }
                ]
            }
        ),
        provider="fixture",
        model="fixture",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    verifier = ClaimEntailmentService(llm)
    result = await GroundingService(ChatConfig(), entailment=verifier).map_claims(
        assertion if reviewed else assertion + " [1]",
        [evidence],
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": assertion,
                "requirement_ids": ["R1"],
                "proof_ids": [str(evidence.chunk_id)],
            }
        ]
        if reviewed
        else None,
    )
    assert llm.generate.await_count == 1
    assert result.grounded is False
    assert result.claims[0]["assertion_id"]
    assert result.claims[0]["verifier_failures"][0]["dimension"] == dimension
    assert result.claims[0]["verifier_failures"][0]["evidence_binding"] == [str(evidence.chunk_id)]
    assert "provider text must not leak" not in json.dumps(result.claims)


@pytest.mark.parametrize("dimension,proof,assertion", DIMENSIONS)
async def test_dimension_positive_exact_proof(dimension, proof, assertion):
    del dimension, assertion
    evidence = source(proof, reviewed=True)
    result = await GroundingService(ChatConfig()).map_claims(
        proof,
        [evidence],
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": proof,
                "requirement_ids": ["R1"],
                "proof_ids": [str(evidence.chunk_id)],
            }
        ],
    )
    assert result.grounded is True
    assert result.claims[0]["verification"] == "supported"
    assert result.claims[0]["verifier_failures"] == []


def test_atomic_ids_are_stable_and_citations_are_rendered_after_verification():
    evidence = source("The deadline is 30 June. The filing fee is Tk 100.", reviewed=True)
    content = json.dumps(
        {
            "segments": [
                {
                    "assertion_id": "A1",
                    "text": evidence.content,
                    "requirement_ids": ["R1"],
                    "proof_ids": [str(evidence.chunk_id)],
                }
            ]
        }
    )
    first = _render_structured_answer(content, [evidence], render_citations=False)
    second = _render_structured_answer(content, [evidence], render_citations=False)
    assert first == second and first is not None
    assert "[1]" not in first[0]
    assert [row["assertion_id"] for row in first[1]["segments"]] == ["A1.1", "A1.2"]


def test_typed_notice_is_separate_from_negative_enactment_fact():
    draft = AnswerDraft.model_validate(
        {
            "segments": [
                {
                    "text": "The speech proposes a threshold.",
                    "requirement_ids": ["R1"],
                    "proof_ids": ["source"],
                }
            ],
            "notices": [{"kind": "proposal_scope", "proof_ids": ["source"]}],
        }
    )
    assert len(draft.segments) == 1 and draft.notices[0].kind == "proposal_scope"
    assert "never enacted" not in draft.model_dump_json()


def budget_sources():
    data = load_captured_fixture("phase2_captured_budget_speech_v1.json")
    provenance = data["provenance"]
    ids = [str(row["id"]) for row in data["rows"] if row["chunk_index"] in (180, 181)]
    return [
        ContextChunk(
            chunk_id=uuid.UUID(row["id"]),
            document_id=uuid.UUID(row["document_id"]),
            chunk_index=row["chunk_index"],
            content=row["content"],
            score=1.0,
            semantic_score=1.0,
            filename=provenance["source"]["filename"],
            chunk_hash=row["extracted_content_sha256"],
            page_number=row["page_number"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            metadata={
                **row["metadata"],
                "project_id": provenance["build"]["project_id"],
                "index_build_id": provenance["build"]["id"],
                "source_metadata_generation": 35,
                "source_type": provenance["source"]["source_type"],
                "source_title": provenance["source"]["title"],
                "source_revision_id": provenance["source"]["source_revision_id"],
                "proof_unit_chunk_ids": ids if row["chunk_index"] in (180, 181) else [row["id"]],
                "reviewed_proof": [
                    {"requirement_id": "R1", "fulfillment": "full", "quote": row["content"]}
                ],
            },
        )
        for row in data["rows"]
    ]


def test_captured_budget_speech_and_table_units_are_indivisible():
    data = load_captured_fixture("phase2_captured_budget_speech_v1.json")
    for row in data["rows"]:
        assert (
            hashlib.sha256(row["content"].encode()).hexdigest() == row["extracted_content_sha256"]
        )
    header, table, category, bands = budget_sources()
    assert header.chunk_index == 180 and table.chunk_index == 181
    assert "BDT 375,000" in table.content and "BDT 450,000" in table.content
    assert "Women taxpayers" in category.content and "Next BDT 300,000" in bands.content
    total = len(header.content) + len(table.content)
    assert ContextBuilder(ChatConfig(context_char_budget=total - 1)).select([header, table]) == []
    selected = ContextBuilder(ChatConfig(context_char_budget=total)).select([header, table])
    assert selected == [header, table]
    assert ContextBuilder(ChatConfig(context_char_budget=total)).select([table]) == []


@pytest.mark.parametrize(
    "text,supported",
    [
        ("The speech proposes a tax-free threshold of BDT 375,000 for AY 2026-27.", True),
        ("The speech proposes a tax-free threshold of BDT 450,000 for AY 2030-31.", True),
        ("The speech proposes a tax-free threshold of BDT 375,000 for AY 2030-31.", False),
        ("The speech proposes a tax-free threshold of BDT 450,000 for AY 2026-27.", False),
        ("The speech proposes a filing fee of BDT 375,000 for AY 2026-27.", False),
        ("The speech proposes women taxpayers a threshold of BDT 375,000 for AY 2026-27.", False),
        ("The enacted current law establishes a threshold of BDT 375,000 for AY 2026-27.", False),
        ("The speech's BDT 375,000 proposal was never enacted.", False),
    ],
)
async def test_attested_budget_table_bound_verification_even_with_semantic_supported(
    text, supported
):
    chunks = budget_sources()[:2]
    llm = AsyncMock()
    llm.supports_output_contract = True
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps({"verdicts": ["supported"]}),
        provider="fixture",
        model="fixture",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    result = await GroundingService(
        ChatConfig(), entailment=ClaimEntailmentService(llm)
    ).map_claims(
        text,
        chunks,
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": text,
                "requirement_ids": ["R1"],
                "proof_ids": [str(c.chunk_id) for c in chunks],
            }
        ],
    )
    assert llm.generate.await_count == 1
    assert result.grounded is supported
    assert all(claim["verification"] == "supported" for claim in result.claims) is supported


def test_complete_period_precedes_local_currency_classification():
    from app.modules.conversations.quantities import normalize_quantities

    quantities = normalize_quantities("AY 2026-27 | Tk 400,000\nAY 2025\u201326\nTk 350,000")
    assert [q.kind for q in quantities] == [
        "period",
        "period",
        "money",
        "period",
        "period",
        "money",
    ]
    assert all(not q.explicit_currency for q in quantities if q.kind == "period")


@pytest.mark.parametrize(
    "operation,result",
    [
        ("addition", "15"),
        ("subtraction", "5"),
        ("multiplication", "50"),
        ("division", "2"),
        ("minimum", "5"),
        ("maximum", "10"),
    ],
)
def test_decimal_graph_operand_provenance(operation, result):
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "N1",
                    "operation": operation,
                    "operands": ["input:salary", "source:cap"],
                    "result": result,
                }
            ]
        }
    )
    assert graph.verify({"salary": Decimal(10)}, {"cap": Decimal(5)}) == {"N1": Decimal(result)}
    with pytest.raises(ValueError):
        graph.verify({"salary": Decimal(10)}, {})


def test_ordered_brackets_prior_nodes_and_literal_rejection():
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "tax",
                    "operation": "ordered_brackets",
                    "operands": ["input:income", "source:width", "source:rate"],
                    "result": "10",
                },
                {
                    "id": "balance",
                    "operation": "subtraction",
                    "operands": ["node:tax", "input:credit"],
                    "result": "7",
                },
            ]
        }
    )
    assert (
        graph.verify(
            {"income": Decimal(100), "credit": Decimal(3)},
            {"width": Decimal(100), "rate": Decimal(".1")},
        )["balance"]
        == 7
    )
    changed = graph.model_copy(deep=True)
    changed.nodes[0] = changed.nodes[0].model_copy(
        update={"operands": ["100", "source:width", "source:rate"]}
    )
    with pytest.raises(ValueError):
        changed.verify({}, {"width": Decimal(100), "rate": Decimal(".1")})


async def test_shared_correction_allowances_across_parallel_branches():
    work = RequestWork(uuid.uuid4())

    async def branch(kind):
        await asyncio.sleep(0)
        return work.claim_correction(kind)

    assert sum(await asyncio.gather(*(branch("malformed") for _ in range(4)))) == 1
    assert sum(await asyncio.gather(*(branch("semantic") for _ in range(4)))) == 1
    assert work.snapshot()["counts"]["malformed_correction_exchanges"] == 1


async def test_outer_timer_promotes_once_from_original_start(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    work = RequestWork(uuid.uuid4())
    work.configure_execution("adaptive_v1")
    assert work.request_deadline == 145 and work.recovery_deadline == 113
    async with work.execution_timeout():
        before = work._outer_timeout.when()
        clock[0] = 110
        assert not work.promote("slow_provider", recoverable=False)
        assert not work.promote("absent_rule", recoverable=True, known_corpus_gap=True)
        assert work.promote("R-effective-amendment", recoverable=True)
        assert work.request_deadline == 220 and work.deadline == 218
        assert work._outer_timeout.when() > before
        assert not work.promote("second", recoverable=True)


def test_measured_p95_freezes_larger_estimate_and_unused_reserves():
    work = RequestWork(uuid.uuid4())
    work.configure_execution("adaptive_v1")
    samples = [
        {
            "message_id": str(index),
            "metadata": {
                "lifecycle": {
                    "deadline": {"budget_class": "simple"},
                    "spans": {
                        "items": [
                            {
                                "name": "claim_verification",
                                "elapsed_ms": 14000,
                                "outcome": "completed",
                            }
                        ]
                    },
                }
            },
        }
        for index in range(20)
    ]
    work.freeze_measured_stages(samples)
    assert work.reserve("verification") == 14
    assert work.stage_estimates["verification"]["origin"] == "measured_p95"
    assert len(work.stage_estimates["verification"]["estimate_id"]) == 64
    work.freeze_measured_stages([])
    assert work.reserve("verification") == 14
    work.complete_stage("coverage")
    assert work.reserve("coverage") == 0 and work.reserve("verification") == 14


@pytest.mark.parametrize("complex_question,credits", [(False, 3), (True, 6)])
def test_two_initial_recovery_searches_and_separate_structure_credits(complex_question, credits):
    work = RequestWork(uuid.uuid4())
    work.configure_execution("adaptive_v1", complex_question=complex_question)
    schedule = RecoverySchedule(work.recovery_deadline)
    schedule.action_limits["search"] = work.recovery_search_credits
    for index in range(credits):
        assert schedule.admit(
            "search",
            requirement_ids=["R1"],
            fingerprint=str(index),
            expected_change="named requirement",
        )
    assert not schedule.admit(
        "search", requirement_ids=["R1"], fingerprint="extra", expected_change="named requirement"
    )
    for index in range(2):
        assert schedule.admit(
            "structure",
            requirement_ids=["R1"],
            fingerprint=str(index),
            expected_change="heading or continuation",
        )
    assert not schedule.admit(
        "structure", requirement_ids=["R1"], fingerprint="extra", expected_change="heading"
    )


@pytest.mark.parametrize("case_index,total_ms", [(0, 27986), (1, 28226), (2, 29984), (3, 28011)])
def test_recorded_q1_q4_budget_replay_preserves_stage_reserves(case_index, total_ms):
    data = load_captured_fixture("phase2_recorded_q1_q4_timings_v1.json")
    case = data["cases"][case_index]
    recorded_repair_for_case(case)
    assert case["server_total_latency_ms"] == total_ms
    work = RequestWork(uuid.uuid4())
    assert requires_complex_execution_budget(normalize_request_scope(case["question"]))
    work.configure_execution(
        "adaptive_v1",
        complex_question=requires_complex_execution_budget(
            normalize_request_scope(case["question"])
        ),
    )
    assert work.request_deadline - work.started == 120
    assert work.recovery_deadline - work.started == 88
    initial = next(span for span in case["timing_spans"] if span["phase"] == "initial")
    planning = next(span for span in case["timing_spans"] if span["name"] == "recovery_planning")
    end = (
        max(
            initial["start_offset_ms"] + initial["elapsed_ms"],
            planning["start_offset_ms"] + planning["elapsed_ms"],
        )
        / 1000
    )
    assert end < work.recovery_deadline - work.started
    assert 120 - end > sum(
        work.reserve(stage) for stage in ("coverage", "generation", "verification", "persistence")
    )
    assert case["terminal_reason"] == "recovery_deadline_exceeded"
    assert data["live_calls_made"] is False


def test_legacy_and_explicit_adaptive_immutable_revisions():
    def resolve(execution):
        record = ConfigRevisionRecord(
            id=uuid.uuid4(),
            revision_number=1,
            configuration_hash="a" * 64,
            configuration={"execution": execution},
            schema_version=2,
        )
        return resolve_project_ai_config(Settings(), record).configuration

    legacy = resolve({"profile_id": "standard"})
    adaptive = resolve({"profile_id": "standard", "execution_policy": "adaptive_v1"})
    assert legacy.chat.execution_policy == "legacy"
    assert adaptive.chat.execution_policy == "adaptive_v1"
    assert legacy.chat.execution_policy == "legacy"


@pytest.mark.parametrize("streamed", [False, True])
async def test_adaptive_regular_sse_persisted_get_parity(streamed):
    service, provider, conversation, repository, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    if streamed:
        events = [
            item
            async for item in service.stream_message(
                conversation.id, MessageSendRequest(content=question)
            )
        ]
        done = next(
            item for item in events if isinstance(item, dict) and item.get("event") == "done"
        )
        answer = MessageResponse.from_message(repository.add.call_args.args[0])
        assert done["terminal_outcome"] == answer.terminal_outcome.model_dump(mode="json")
        assert done["claims"] == [claim.model_dump(mode="json") for claim in answer.claims]
    else:
        answer = (
            await service.send_message(conversation.id, MessageSendRequest(content=question))
        ).assistant_message
        assert (
            MessageResponse.from_message(repository.add.call_args.args[0]).claims == answer.claims
        )
    assert answer.terminal_outcome.outcome == "answered" and answer.grounded is True
    assert "18 months" in answer.content and "15 months" in answer.content
    assert all(claim.assertion_id and claim.verification == "supported" for claim in answer.claims)
    assert answer.metadata["lifecycle"]["deadline"]["policy"] == "adaptive_v1"
    assert provider.purposes.count("answer_shape_correction") <= 1


async def test_adaptive_evaluation_uses_same_production_finalization():
    service, provider, conversation, _, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    regular = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    evidence = provider.source
    _, evaluation_provider, _, _, _ = journey()
    evaluation_provider.source = evidence
    settings = Settings(
        chat=service._chat_config, retrieval=service._retrieval_config, llm=service._llm_config
    )
    answer = await GroundedEvaluationAnswerAdapter(
        settings=settings, llm=evaluation_provider, project_id=conversation.project_id
    ).answer_for_case(
        profile="semantic",
        request=MessageSendRequest(content=question),
        provenance={
            "index_build_id": evidence.metadata["index_build_id"],
            "source_metadata_generation": evidence.metadata["source_metadata_generation"],
        },
        hits=[
            QualityHit(
                chunk_id=evidence.chunk_id,
                document_id=evidence.document_id,
                content=evidence.content,
                score=evidence.score,
                semantic_score=evidence.semantic_score,
                filename=evidence.filename,
                chunk_index=evidence.chunk_index,
                metadata=evidence.metadata,
            )
        ],
    )
    assert answer.answer == regular.content
    assert answer.execution["terminal"] == regular.terminal_outcome.model_dump(mode="json")


async def test_known_corpus_gap_completes_precise_limitation_without_search_or_promotion():
    service, provider, conversation, _, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    initial = await service._retrieval.retrieve()
    from app.dependencies.conversations import SearchServiceRetrievalAdapter

    diagnostics = {
        **initial.diagnostics,
        "source_policy_effective_mode": "enforce",
        "modifies_expansion_records": [
            {
                "relationship_id": str(uuid.uuid4()),
                "relationship_type": "modifies",
                "modifier_revision_id": str(uuid.uuid4()),
                "outcome": "not_in_active_index",
                "target_provisions": ["The commencement link for the required amendment"],
            }
        ],
    }
    search = SimpleNamespace(
        _project_id=conversation.project_id,
        search=AsyncMock(
            return_value=SimpleNamespace(
                results=[], diagnostics=SimpleNamespace(model_dump=lambda **kw: diagnostics)
            )
        ),
    )
    produced = await SearchServiceRetrievalAdapter(search).retrieve(query=question, top_k=5)
    initial.diagnostics.update(produced.diagnostics)
    assert initial.diagnostics["known_corpus_gap_binding"]["project_id"] == str(
        conversation.project_id
    )
    service._retrieval.retrieve.reset_mock()
    requirements, _ = provider.review()
    provider.review = lambda: (
        requirements,
        {
            "complete": False,
            "missing": ["The commencement link for the required amendment"],
            "checks": [],
        },
    )
    answer = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    assert answer.terminal_outcome.outcome == "insufficient_evidence"
    assert answer.terminal_outcome.reason_code == "known_corpus_gap"
    assert answer.terminal_outcome.retryable is False
    assert "commencement link" in answer.content
    assert service._retrieval.retrieve.await_count == 1
    assert not service._work.promoted
    assert "answer_generation" not in provider.purposes


async def test_one_semantic_repair_uses_only_approved_source_facts():
    service, _provider, conversation, _, question = journey(
        "threshold", failure="wrong_period", combined=True
    )
    service._chat_config.execution_policy = "adaptive_v1"
    answer = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    assert service._work.counts["semantic_repairs"] == 1
    assert service._work.counts["malformed_correction_exchanges"] == 0
    assert "AY 2025-26" not in answer.content
    assert answer.grounded is True
    assert all(claim.verification == "supported" for claim in answer.claims)
    assert answer.metadata["execution"]["rejected_assertions"] >= 1


async def test_actual_malformed_output_exchange_is_shared_across_branches():
    from pydantic import ValidationError

    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    work = RequestWork(uuid.uuid4())
    work.configure_execution("adaptive_v1", complex_question=True)
    llm = AsyncMock()
    llm.supports_output_contract = True
    issued = [0]

    async def respond(*args, **kwargs):
        del args, kwargs
        issued[0] += 1
        position = issued[0]
        await asyncio.sleep(0)
        content = (
            "malformed"
            if position <= 2
            else json.dumps({"complete": False, "missing": ["required rule"], "checks": []})
        )
        return ChatCompletionResult(
            content=content,
            provider="fixture",
            model="fixture",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    llm.generate.side_effect = respond

    async def branch():
        return await _validated_completion(
            llm,
            [ChatMessage(ChatRole.USER, "Review supplied evidence")],
            schema=CoverageVerdict,
            max_tokens=1024,
        )

    with work.attached():
        outcomes = await asyncio.gather(branch(), branch(), return_exceptions=True)
    assert sum(isinstance(item, ChatCompletionResult) for item in outcomes) == 1
    assert sum(isinstance(item, ValidationError) for item in outcomes) == 1
    assert llm.generate.await_count == 3
    assert work.counts["malformed_correction_exchanges"] == 1
    assert not work.claim_correction("malformed")


@pytest.mark.parametrize("reference", ["5", "node:future", "source:unknown"])
def test_graph_rejects_literal_forward_and_unverified_source_operands(reference):
    graph = CalculationGraph.model_validate(
        {"nodes": [{"id": "N1", "operation": "addition", "operands": [reference], "result": "5"}]}
    )
    with pytest.raises(ValueError):
        graph.verify({}, {})


def test_division_by_zero_and_uncovered_brackets_fail_closed():
    for graph in [
        {
            "nodes": [
                {
                    "id": "N1",
                    "operation": "division",
                    "operands": ["input:income", "source:zero"],
                    "result": "0",
                }
            ]
        },
        {
            "nodes": [
                {
                    "id": "N1",
                    "operation": "ordered_brackets",
                    "operands": ["input:income", "source:width", "source:rate"],
                    "result": "5",
                }
            ]
        },
    ]:
        with pytest.raises(ValueError):
            CalculationGraph.model_validate(graph).verify(
                {"income": Decimal(100)},
                {"zero": Decimal(0), "width": Decimal(50), "rate": Decimal(".1")},
            )


async def execute_contract(service, provider, conversation, repository, question, transport):
    if transport == "evaluation":
        evidence = provider.source
        answer = await GroundedEvaluationAnswerAdapter(
            settings=Settings(
                chat=service._chat_config,
                retrieval=service._retrieval_config,
                llm=service._llm_config,
            ),
            llm=provider,
            project_id=conversation.project_id,
        ).answer_for_case(
            profile="semantic",
            request=MessageSendRequest(content=question),
            provenance={
                "index_build_id": evidence.metadata["index_build_id"],
                "source_metadata_generation": evidence.metadata["source_metadata_generation"],
            },
            work=service._work,
            execution_retrieval=service._retrieval,
            hits=[
                QualityHit(
                    chunk_id=evidence.chunk_id,
                    document_id=evidence.document_id,
                    content=evidence.content,
                    score=evidence.score,
                    semantic_score=evidence.semantic_score,
                    filename=evidence.filename,
                    chunk_index=evidence.chunk_index,
                    metadata=evidence.metadata,
                )
            ],
        )
        return {
            "content": answer.answer,
            "claims": answer.claims,
            "execution": answer.execution,
            "notices": answer.notices,
            "terminal_outcome": answer.execution["terminal"],
            "grounded": answer.grounded,
        }
    if transport == "sse":
        events = [
            item
            async for item in service.stream_message(
                conversation.id, MessageSendRequest(content=question)
            )
        ]
        done = next(
            item for item in events if isinstance(item, dict) and item.get("event") == "done"
        )
        saved = MessageResponse.from_message(repository.add.call_args.args[0])
        assert done["claims"] == [c.model_dump(mode="json") for c in saved.claims]
        assert done["notices"] == [n.model_dump(mode="json") for n in saved.notices]
        assert done["terminal_outcome"] == saved.terminal_outcome.model_dump(mode="json")
        return saved.model_dump(mode="json")
    response = (
        await service.send_message(conversation.id, MessageSendRequest(content=question))
    ).assistant_message
    saved = MessageResponse.from_message(repository.add.call_args.args[0])
    assert saved.claims == response.claims and saved.content == response.content
    return saved.model_dump(mode="json") if transport == "get" else response.model_dump(mode="json")


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
@pytest.mark.parametrize(
    "kind", ["documentary_scope", "proposal_scope", "unresolved_applicability"]
)
async def test_typed_scope_notice_real_finalization_transport_parity(kind, transport):
    service, provider, conversation, repository, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    original = json.loads(provider.answer())
    original["notices"] = [{"kind": kind, "proof_ids": [str(provider.source.chunk_id)]}]
    provider.answer = lambda: json.dumps(original)
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["grounded"] is True
    assert kind in [item["kind"] for item in answer["notices"]]
    assert all("never enacted" not in item["text"] for item in answer["notices"])
    assert all(claim["verification"] == "supported" for claim in answer["claims"])


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
@pytest.mark.parametrize(
    "invented",
    [
        "Threshold: BDT 999999",
        "# Every company pays a lunar registration fee.",
        "| Filing fee | BDT 999999 |",
    ],
)
async def test_every_typed_factual_row_is_attempted_and_cannot_publish(invented, transport):
    service, provider, conversation, repository, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    draft = json.loads(provider.answer())
    draft["segments"].append(
        {
            "assertion_id": "invented",
            "text": invented,
            "requirement_ids": ["R1"],
            "proof_ids": [str(provider.source.chunk_id)],
        }
    )
    provider.answer = lambda: json.dumps(draft)
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert invented not in answer["content"] and "999999" not in answer["content"]
    execution = answer.get("execution") or answer["metadata"]["execution"]
    assert execution["rejected_assertions"] >= 1
    assert all(claim["verification"] == "supported" for claim in answer["claims"])


@pytest.mark.parametrize(
    "text",
    [
        "10 * 5 = 999",
        "10 / 5 = 5",
        "minimum(10, 5) = 10",
        "ordered_brackets(10, 5, 1) = 5",
    ],
)
async def test_graph_actual_assertion_gate_cannot_be_overridden_by_semantic_support(text):
    service, provider, conversation, repository, _question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    draft = json.loads(provider.answer())
    draft["calculations"] = {
        "nodes": [
            {
                "id": "N1",
                "operation": "minimum",
                "operands": ["input:q0", "input:q1"],
                "result": "5",
            }
        ]
    }
    draft["segments"].append(
        {
            "assertion_id": "bad-math",
            "text": text,
            "requirement_ids": ["R1"],
            "proof_ids": [str(provider.source.chunk_id)],
            "calculation_references": ["N1"],
        }
    )
    provider.answer = lambda: json.dumps(draft)
    answer = await execute_contract(
        service,
        provider,
        conversation,
        repository,
        ("Compare the first and subsequent AGM deadlines. Supplied inputs: 10 and 5."),
        "regular",
    )
    assert text not in answer["content"]
    assert answer["claims"] == [] and answer["grounded"] is False
    assert answer["terminal_outcome"]["outcome"] == "verification_failed"
    assert answer["metadata"]["execution"]["rejected_assertions"] >= 3


@pytest.mark.parametrize("case_index", range(4))
@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
async def test_recorded_q1_q4_actual_production_completed_limitation(
    case_index, transport, monkeypatch
):
    from app.platform.providers.errors import ProviderTimeoutError
    from app.platform.providers.request_work import current_request_purpose

    data = load_captured_fixture("phase2_recorded_q1_q4_timings_v1.json")
    case = data["cases"][case_index]
    original_repair = recorded_repair_for_case(case)
    requirements = original_repair["requirements"]
    missing = [item["description"] for item in requirements]
    coverage = {
        "complete": False,
        "missing": missing,
        "checks": [
            {
                "requirement_id": item["requirement_id"],
                "description": item["description"],
                "supported": False,
                "fulfillment": "none",
                "evidence": [],
            }
            for item in requirements
        ],
    }
    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr(
        "app.modules.conversations.services.evidence_repair_service.monotonic", lambda: clock[0]
    )
    service, provider, conversation, repository, _ = journey("threshold")
    service._work = RequestWork(conversation.project_id)
    service._runner._work = service._work
    service._chat_config.execution_policy = "adaptive_v1"
    initial = next(span for span in case["timing_spans"] if span["phase"] == "initial")
    planning = next(span for span in case["timing_spans"] if span["name"] == "recovery_planning")
    planning_end = (planning["start_offset_ms"] + planning["elapsed_ms"]) / 1000
    initial_end = (initial["start_offset_ms"] + initial["elapsed_ms"]) / 1000
    observed_recovery = [span for span in case["timing_spans"] if span["phase"] == "recovery"]
    recovery_end = max((s["start_offset_ms"] + s["elapsed_ms"]) / 1000 for s in observed_recovery)
    purposes = []

    async def controlled_generate(messages, **kwargs):
        del messages, kwargs
        purpose = current_request_purpose()
        purposes.append(purpose)
        if purpose in {"recovery_planning", "structured_response_retry"}:
            clock[0] = max(clock[0], 100 + planning_end)
            content = {
                "requirements": requirements,
                "queries": [
                    {"query": query, "requirement_ids": [requirements[0]["requirement_id"]]}
                    for query in original_repair["queries"][:2]
                ],
                "coverage": coverage,
            }
        elif purpose == "coverage_review":
            clock[0] = max(clock[0], 100 + recovery_end + 4.020)
            content = coverage
        elif purpose == "scenario_input_review":
            content = {
                "gaps": [
                    {"gap_index": index, "kind": "source_rule"} for index in range(len(missing))
                ]
            }
        else:
            content = {"queries": []}
        return ChatCompletionResult(
            content=json.dumps(content),
            provider="fixture",
            model="fixture",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(10, 5),
        )

    provider.generate = controlled_generate
    initial_result = service._retrieval.retrieve.return_value
    retrieval_calls = [0]

    async def recorded_retrieve(**kwargs):
        del kwargs
        retrieval_calls[0] += 1
        if retrieval_calls[0] == 1:
            clock[0] = max(clock[0], 100 + initial_end)
            return initial_result
        clock[0] = max(clock[0], 100 + recovery_end)
        if case_index != 2:
            raise ProviderTimeoutError(
                "Captured independent retrieval interruption",
                provider_name="fixture",
                context={"reason": "provider_timeout"},
            )
        return initial_result

    service._retrieval.retrieve.side_effect = recorded_retrieve
    # Evaluation receives the same initial hits and initial elapsed offset; its
    # runner then executes mandatory review using the same recorded clock.
    if transport == "evaluation":
        clock[0] = 100 + initial_end
    answer = await execute_contract(
        service, provider, conversation, repository, case["question"], transport
    )
    assert answer["terminal_outcome"]["outcome"] == "insufficient_evidence"
    assert answer["terminal_outcome"]["retryable"] is False
    assert any(label in answer["content"] for label in missing)
    assert answer["claims"] == []
    assert "recovery_planning" in purposes
    assert "answer_generation" not in purposes
    if case_index == 2:
        assert "coverage_review" in purposes
    assert clock[0] - 100 < 120
    if transport != "evaluation":
        lifecycle = answer["metadata"]["lifecycle"]
        assert lifecycle["persistence_completed"] is True
        assert lifecycle["deadline"]["budget_class"] == "complex"
        assert any(
            span["name"] == "persistence" and span["outcome"] == "completed"
            for span in lifecycle["spans"]["items"]
        )


@pytest.mark.parametrize("fits", [True, False])
async def test_affordability_promotion_changes_actual_outer_timer_only_when_required(
    fits, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    work = RequestWork(uuid.uuid4())
    work.configure_execution("adaptive_v1")
    async with work.execution_timeout():
        before = work._outer_timeout.when()
        clock[0] = 105 if fits else 111
        assert work.promote("R1-required-continuation", recoverable=True, required_seconds=6) is (
            not fits
        )
        assert (work._outer_timeout.when() == before) is fits
        assert work.request_deadline == (145 if fits else 220)
        if not fits:
            assert work.recovery_deadline == 188
            assert not work.promote("R2", recoverable=True, required_seconds=6)


def test_measured_persistence_samples_shared_budget_contract_and_provenance():
    samples = [
        {
            "message_id": str(uuid.uuid4()),
            "metadata": {
                "lifecycle": {
                    "deadline": {"budget_class": "complex"},
                    "spans": {
                        "items": [
                            {"name": "persistence", "elapsed_ms": 4500, "outcome": "completed"}
                        ]
                    },
                }
            },
        }
        for _ in range(20)
    ]
    chat = RequestWork(uuid.uuid4())
    evaluation = RequestWork(chat.project_id)
    evaluation.started = chat.started
    for work in (chat, evaluation):
        work.configure_execution("adaptive_v1", complex_question=True)
        work.freeze_measured_stages(samples)
    assert chat.stage_estimates == evaluation.stage_estimates
    assert chat.reserve("persistence") == evaluation.reserve("persistence") == 4.5
    assert chat.recovery_deadline == evaluation.recovery_deadline
    assert chat.stage_estimates["persistence"]["origin"] == "measured_p95"
    assert len(chat.stage_estimates["persistence"]["estimate_id"]) == 64


async def test_batch_discovery_cutoff_retains_completed_sibling_and_closes_pending(monkeypatch):
    import time
    from contextlib import asynccontextmanager

    from app.dependencies.conversations import SearchServiceRetrievalAdapter

    closed = []
    snapshot = {"index_build_id": "build", "discovery_deadline": time.perf_counter() + 0.03}

    @asynccontextmanager
    async def sessions():
        try:
            yield object()
        finally:
            closed.append(True)

    async def retrieve(adapter, **request):
        del adapter
        if request["query"] == "complete":
            return ContextRetrievalResult([source("Completed source proof.")], snapshot)
        await asyncio.Event().wait()

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        object(), session_factory=sessions, branch_factory=lambda session, pinned: object()
    )
    results = await adapter.retrieve_batch(
        [{"query": "complete"}, {"query": "pending"}], snapshot=snapshot
    )
    assert len(results[0].chunks) == 1 and results[1].chunks == []
    assert len(closed) == 2


def test_structural_closure_cannot_erase_another_requirements_gap():
    from app.modules.conversations.services.evidence_coverage import CoverageDelta, _Check
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    a, b, c = (
        source("First incomplete provision."),
        source("Second incomplete provision."),
        source("Governing heading: first completed provision."),
    )
    proof = _TurnProofMap(
        [
            EvidenceRequirement(requirement_id="R1", description="first"),
            EvidenceRequirement(requirement_id="R2", description="second"),
        ]
    )
    checks = [
        _Check(
            requirement_id=identity,
            supported=False,
            fulfillment="none",
            unresolved_facets=[gap],
            evidence=[],
        )
        for identity, gap in [("R1", "R1 governing heading"), ("R2", "R2 missing footnote")]
    ]
    proof.remember_gaps(["R1 governing heading", "R2 missing footnote"], checks)
    delta = CoverageDelta.model_validate(
        {
            "complete": False,
            "missing": ["R2 missing footnote"],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "fulfillment": "full",
                    "resolved_gaps": ["R1 governing heading", "R2 missing footnote"],
                    "evidence": [{"chunk_id": str(c.chunk_id), "quote": c.content}],
                }
            ],
        }
    )
    merged = proof.merge_delta(delta, {"R1"}, [a, b, c], [])
    assert "R2 missing footnote" in merged.missing
    assert "R1 governing heading" not in merged.missing


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("telemetry_failure", ["none", "error", "deadline", "cancelled"])
async def test_late_persistence_publishes_once_and_metadata_failure_preserves_acknowledged_answer(
    transport, telemetry_failure, monkeypatch
):
    from app.modules.conversations.repositories.message_repository import MessageRepository
    from app.platform.providers.errors import ProviderTimeoutError

    service, provider, conversation, original_repository, question = journey()
    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr(
        "app.modules.conversations.services.evidence_repair_service.monotonic", lambda: clock[0]
    )
    service._work = RequestWork(conversation.project_id)
    service._runner._work = service._work
    service._chat_config.execution_policy = "adaptive_v1"
    question = "Compare the first AGM deadline with the subsequent AGM interval."

    async def commit_with_acquisition_delay():
        # User persistence occurs at the start. Assistant persistence is admitted
        # after verification, using the final reserved window.
        if original_repository.add.call_count >= 2:
            clock[0] = 219.0

    service._session.commit.side_effect = commit_with_acquisition_delay

    class MeasuringRepository(MessageRepository):
        def __init__(self):
            super().__init__(service._session, conversation.project_id)
            self.add = original_repository.add
            self.flush = original_repository.flush
            self.list_recent_for_conversation = original_repository.list_recent_for_conversation
            self.completed = []

        async def record_completed_persistence(self, message, metadata):
            self.completed.append(metadata)
            if telemetry_failure == "cancelled":
                raise asyncio.CancelledError("measurement interrupted")
            if telemetry_failure == "error":
                raise RuntimeError("measurement write failed")
            if telemetry_failure == "deadline":
                raise ProviderTimeoutError(
                    "metadata deadline",
                    provider_name="database",
                    context={"reason": "request_deadline_exceeded"},
                )

        async def execution_samples(self, *args, **kwargs):
            return []

    from unittest.mock import MagicMock

    scope_query = MagicMock()
    scope_query.all.return_value = []
    service._session.scalars.return_value = scope_query
    repository = MeasuringRepository()
    service._message_repository = repository
    service._config_snapshot_id = uuid.uuid4()
    generate = provider.generate

    async def late_verification(messages, **kwargs):
        from app.platform.providers.request_work import current_request_purpose

        purpose = current_request_purpose()
        result = await generate(messages, **kwargs)
        if purpose == "recovery_planning":
            # This question is complex; the coverage finishes in its protected
            # window and releases its unused reserve before generation.
            clock[0] = 185.0
        elif purpose == "answer_generation":
            clock[0] = 205.0
        elif purpose == "claim_verification":
            clock[0] = 217.0
        return result

    provider.generate = late_verification
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["terminal_outcome"]["outcome"] == "answered"
    assert answer["grounded"] is True and answer["claims"]
    assert repository.add.call_count == 2
    assert len(repository.completed) == 1
    measurement = repository.completed[0]["lifecycle"]
    persistence = [span for span in measurement["spans"]["items"] if span["name"] == "persistence"]
    assert persistence and persistence[-1]["outcome"] == "completed"
    assert persistence[-1]["elapsed_ms"] == 2000
    assert clock[0] <= 220
    lifecycle = answer["metadata"]["lifecycle"]
    assert lifecycle["answer_persisted"] is True
    assert lifecycle["persistence_completed"] is (telemetry_failure == "none")
    assert lifecycle["complete_turn_measurement_available"] is (telemetry_failure == "none")
    if telemetry_failure == "none":
        assert lifecycle["processing_ms"] == 119000
    else:
        assert lifecycle["processing_ms"] == 117000


async def test_chat_and_evaluation_read_same_completed_sample_contract(monkeypatch):
    from app.modules.conversations.repositories.message_repository import MessageRepository

    reads = []
    samples = [
        {
            "message_id": str(uuid.uuid4()),
            "metadata": {
                "lifecycle": {
                    "deadline": {"budget_class": "complex"},
                    "spans": {
                        "items": [
                            {"name": "persistence", "elapsed_ms": 5000, "outcome": "completed"}
                        ]
                    },
                }
            },
        }
        for _ in range(20)
    ]

    async def samples_read(repository, snapshot_id=None, **kwargs):
        reads.append((repository._project_id, snapshot_id, kwargs))
        return samples

    monkeypatch.setattr(MessageRepository, "execution_samples", samples_read)
    service, provider, conversation, _repository, question = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    question = "Compare the first AGM deadline with the subsequent AGM interval."
    service._work.configure_execution("adaptive_v1", complex_question=True)
    service._config_snapshot_id = uuid.uuid4()
    service._message_repository = MessageRepository(service._session, conversation.project_id)
    await service._freeze_stage_estimates()
    _, evaluation_provider, _, _, _ = journey()
    evaluation_provider.source = provider.source
    evidence = provider.source
    answer = await GroundedEvaluationAnswerAdapter(
        settings=Settings(
            chat=service._chat_config, retrieval=service._retrieval_config, llm=service._llm_config
        ),
        llm=evaluation_provider,
        session=service._session,
        project_id=conversation.project_id,
    ).answer_for_case(
        profile="semantic",
        request=MessageSendRequest(content=question),
        provenance={"configuration_hash": "a" * 64},
        hits=[
            QualityHit(
                chunk_id=evidence.chunk_id,
                document_id=evidence.document_id,
                content=evidence.content,
                score=1,
                semantic_score=1,
                filename=evidence.filename,
                chunk_index=evidence.chunk_index,
                metadata=evidence.metadata,
            )
        ],
    )
    assert len(reads) == 2 and reads[0][0] == reads[1][0] == conversation.project_id
    assert reads[0][1] == service._config_snapshot_id
    assert reads[1][2]["configuration_hash"] == "a" * 64
    assert service._work.stage_estimates["persistence"]["seconds"] == 5
    assert answer.lifecycle["deadline"]["stage_estimates"]["persistence"]["seconds"] == 5


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
async def test_hard_deadline_failure_retains_verified_attempts_and_persists_terminal_once(
    transport, monkeypatch
):
    from app.platform.providers.errors import ProviderTimeoutError
    from app.platform.providers.request_work import current_request_purpose

    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    monkeypatch.setattr(
        "app.modules.conversations.services.evidence_repair_service.monotonic", lambda: clock[0]
    )
    service, provider, conversation, repository, _ = journey()
    service._chat_config.execution_policy = "adaptive_v1"
    service._work = RequestWork(conversation.project_id)
    service._runner._work = service._work
    generate = provider.generate

    async def interrupted(messages, **kwargs):
        if current_request_purpose() == "claim_verification":
            clock[0] = 217.0
            raise ProviderTimeoutError(
                "Bounded verification interruption",
                provider_name="fixture",
                context={"reason": "request_deadline_exceeded", "phase": "claim_verification"},
            )
        return await generate(messages, **kwargs)

    provider.generate = interrupted
    answer = await execute_contract(
        service,
        provider,
        conversation,
        repository,
        "Compare the first AGM deadline with the subsequent AGM interval.",
        transport,
    )
    assert answer["terminal_outcome"]["outcome"] == "timed_out"
    assert answer["claims"] == [] and answer["grounded"] is False
    assert clock[0] - 100 <= 120
    execution = answer.get("execution") or answer["metadata"]["execution"]
    assert execution["attempted_assertions"] >= 2
    if transport != "evaluation":
        assert repository.add.call_count == 2
        assert answer["metadata"]["lifecycle"]["answer_persisted"] is True
        assert answer["metadata"]["lifecycle"]["complete_turn_measurement_available"] is False


def test_verifier_objects_reject_invalid_dimensions_bindings_and_support_conflicts():
    from pydantic import ValidationError

    from app.modules.conversations.services.claim_entailment_service import EntailmentBatch

    for row in [
        {"status": "supported", "failed_dimensions": ["period"], "evidence_binding": ""},
        {"status": "unsupported", "failed_dimensions": ["invented"], "evidence_binding": ""},
        {"status": "unsupported", "failed_dimensions": ["period"], "evidence_binding": "x" * 1001},
    ]:
        with pytest.raises(ValidationError):
            EntailmentBatch.model_validate({"verdicts": [row]})
    assert (
        EntailmentBatch.model_validate({"verdicts": ["supported"]}).verdicts[0].status
        == "supported"
    )


async def test_recoverable_batch_timeout_preserves_siblings_discovery_window(monkeypatch):
    import time
    from contextlib import asynccontextmanager

    from app.dependencies.conversations import SearchServiceRetrievalAdapter
    from app.platform.providers.errors import ProviderTimeoutError

    snapshot = {"index_build_id": "build", "discovery_deadline": time.perf_counter() + 0.1}
    closed = []

    @asynccontextmanager
    async def sessions():
        try:
            yield object()
        finally:
            closed.append(True)

    async def retrieve(adapter, **request):
        del adapter
        if request["query"] == "timeout":
            raise ProviderTimeoutError("Independent timeout", provider_name="fixture")
        await asyncio.sleep(0.01)
        return ContextRetrievalResult([source("Completed independent proof.")], snapshot)

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        object(), session_factory=sessions, branch_factory=lambda session, pinned: object()
    )
    results = await adapter.retrieve_batch(
        [{"query": "timeout"}, {"query": "success"}], snapshot=snapshot
    )
    assert results[0].chunks == [] and len(results[1].chunks) == 1
    assert len(closed) == 2


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize(
    "proof,assertion",
    [
        ("A public employee may claim the allowance.", "Public employees can claim the allowance."),
        (
            "The allowance applies in AY 2026-27.",
            "AY 2026-27 is the applicable period for this allowance.",
        ),
        (
            "A tenant with rented premises must supply the rental agreement.",
            "Tenants renting premises are required to supply their rental agreement.",
        ),
        (
            "A deduction certificate establishes the source tax credit.",
            "The source tax credit is established by a deduction certificate.",
        ),
        ("The maximum penalty is Tk 100 per day.", "The daily penalty is capped at Tk 100."),
        (
            "The speech proposes a Tk 400,000 threshold.",
            "The document's proposed threshold is Tk 400,000.",
        ),
    ],
)
async def test_ordinary_and_reviewed_faithful_paraphrases_invoke_source_entailment(
    reviewed, proof, assertion
):
    evidence = source(proof, reviewed=reviewed)
    llm = AsyncMock()
    llm.supports_output_contract = True
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps({"verdicts": ["supported"]}),
        provider="fixture",
        model="fixture",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    result = await GroundingService(
        ChatConfig(), entailment=ClaimEntailmentService(llm)
    ).map_claims(
        assertion if reviewed else assertion + " [1]",
        [evidence],
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": assertion,
                "requirement_ids": ["R1"],
                "proof_ids": [str(evidence.chunk_id)],
            }
        ]
        if reviewed
        else None,
    )
    assert llm.generate.await_count == 1
    assert result.grounded is True


@pytest.mark.parametrize("transport", ["regular", "sse", "get", "evaluation"])
@pytest.mark.parametrize("dropped", [False, True])
async def test_published_requirements_and_omitted_limits_agree_across_transports(
    transport, dropped, monkeypatch
):
    from dataclasses import replace

    from app.modules.conversations.grounding_service import GroundingResult
    from app.modules.conversations.services.message_execution_runner_service import (
        MessageExecutionRunner,
    )

    service, provider, conversation, repository, question = journey()
    original_answer = provider.answer

    def omitted_answer():
        draft = json.loads(original_answer())
        if not dropped:
            draft["segments"] = draft["segments"][:1]
        return json.dumps(draft)

    provider.answer = omitted_answer
    if dropped:
        original_map = GroundingService.map_claims

        async def reject_second(self, content, chunks, **kwargs):
            result = await original_map(self, content, chunks, **kwargs)
            claims = [
                {**c, "verification": "unsupported", "grounded": False, "evidence": []}
                if "R2" in c.get("requirement_ids", [])
                else c
                for c in result.claims
            ]
            return GroundingResult(
                claims=claims,
                grounded=bool(claims) and all(c["verification"] == "supported" for c in claims),
                citation_coverage=1.0,
            )

        monkeypatch.setattr(GroundingService, "map_claims", reject_second)
        original_prepare = MessageExecutionRunner.prepare

        async def prepare_without_repair_quote(self, *args, **kwargs):
            prepared = await original_prepare(self, *args, **kwargs)
            selected = [
                replace(
                    chunk,
                    metadata={
                        **chunk.metadata,
                        "reviewed_proof": [
                            row
                            for row in chunk.metadata.get("reviewed_proof", [])
                            if row.get("requirement_id") != "R2"
                        ],
                    },
                )
                for chunk in prepared.selected
            ]
            return replace(prepared, selected=selected)

        monkeypatch.setattr(MessageExecutionRunner, "prepare", prepare_without_repair_quote)

    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    terminal = answer["terminal_outcome"]
    assert terminal["outcome"] == "partial"
    assert terminal["coverage"] == "partial"
    assert terminal["supported_requirement_ids"] == ["R1"]
    assert terminal["unresolved_requirement_ids"] == ["R2"]
    assert answer["grounded"] is True and answer["claims"]
    assert any(
        notice["kind"] == "unfulfilled_requirements"
        and "Maximum interval between subsequent AGMs" in notice["text"]
        for notice in answer["notices"]
    )


def test_required_dependencies_need_published_proof_and_optional_details_do_not():
    from app.modules.conversations.execution_contracts import (
        RequirementGraph,
        published_requirement_fulfillment,
    )

    graph = RequirementGraph.from_coverage(
        {
            "requirements": [
                {"requirement_id": "R1", "description": "Applicability"},
                {"requirement_id": "R2", "description": "Rate", "depends_on": ["R1"]},
                {
                    "requirement_id": "R3",
                    "description": "Example",
                    "origin": "optional_corroboration",
                },
            ]
        }
    )

    def claim(identity):
        return {
            "verification": "supported",
            "grounded": True,
            "evidence": [{"chunk_id": "source"}],
            "requirement_ids": [identity],
        }

    incomplete = published_requirement_fulfillment(graph, [claim("R2")])
    assert incomplete["supported_requirement_ids"] == []
    assert incomplete["unresolved_requirement_ids"] == ["R1", "R2"]
    complete = published_requirement_fulfillment(graph, [claim("R1"), claim("R2")])
    assert complete["supported_requirement_ids"] == ["R1", "R2"]
    assert complete["unresolved_requirement_ids"] == []
