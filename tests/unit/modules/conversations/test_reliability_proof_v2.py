"""Strict wire binding, partial arithmetic and completed transport contracts."""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "reverse",
        "missing",
        "duplicate",
        "foreign_assertion",
        "foreign_proof",
        "foreign_scope",
        "legacy",
    ],
)
async def test_verifier_accepts_only_exact_assertion_proof_and_scope_bindings(mutation):
    llm = AsyncMock()
    llm.supports_output_contract = True

    async def generate(messages, **kwargs):
        items = json.loads(messages[1].content)
        rows = [
            {
                "assertion_id": row["assertion_id"],
                "proof_hashes": row["proof_hashes"],
                "scope_hash": row["scope_hash"],
                "status": "supported",
                "failed_dimensions": [],
                "evidence_binding": "exact",
                "publication_complete": True,
            }
            for row in items
        ]
        if mutation == "reverse":
            rows.reverse()
        if mutation == "missing":
            rows.pop()
        if mutation == "duplicate":
            rows.append(dict(rows[0]))
        if mutation == "foreign_assertion":
            rows[0]["assertion_id"] = "foreign"
        if mutation == "foreign_proof":
            rows[0]["proof_hashes"] = ["f" * 64]
        if mutation == "foreign_scope":
            rows[0]["scope_hash"] = "f" * 64
        data = {"version": "claim.entailment.v2", "verdicts": rows}
        if mutation == "legacy":
            data = {"verdicts": ["supported", "supported"]}
        return ChatCompletionResult(
            content=json.dumps(data),
            provider="fixture",
            model="fixture",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    llm.generate.side_effect = generate
    verifier = ClaimEntailmentService(llm)
    result = await verifier.verify(
        [
            {
                "assertion_id": identity,
                "assertion": "A complete rule.",
                "proof": [{"quote": "A complete rule.", "chunk_id": str(uuid.uuid4())}],
                "canonical_scope": {"requested_periods": [{"start_year": 2026}]},
            }
            for identity in ("A1", "A2")
        ]
    )
    assert result == (["supported"] * 2 if mutation in {"none", "reverse"} else ["unverified"] * 2)


def test_arithmetic_failure_is_local_but_foreign_operands_remain_fatal():
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "bad",
                    "operation": "division",
                    "operands": ["input:a", "input:z"],
                    "result": "0",
                },
                {
                    "id": "dependent",
                    "operation": "addition",
                    "operands": ["node:bad", "input:a"],
                    "result": "10",
                },
                {
                    "id": "good",
                    "operation": "multiplication",
                    "operands": ["input:a", "input:b"],
                    "result": "50",
                },
            ]
        }
    )
    expressions, failures = graph.component_expressions(
        {"a": Decimal(10), "z": Decimal(0), "b": Decimal(5)}, {}
    )
    assert expressions == {"good": "10 * 5 = 50"}
    assert set(failures) == {"bad", "dependent"}
    with pytest.raises(ValueError, match="foreign"):
        graph.component_expressions({"a": Decimal(10), "z": Decimal(0)}, {})


@pytest.mark.parametrize("policy", ["bounded_v2", "bounded_extended_v2"])
@pytest.mark.parametrize("streamed", [False, True])
async def test_new_policy_post_sse_saved_content_parity(policy, streamed):
    service, _provider, conversation, repository, question = journey()
    service._chat_config.execution_policy = policy
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
        assert done["claims"] == [c.model_dump(mode="json") for c in answer.claims]
    else:
        answer = (
            await service.send_message(conversation.id, MessageSendRequest(content=question))
        ).assistant_message
    saved = MessageResponse.from_message(repository.add.call_args.args[0])
    assert saved.content == answer.content
    assert answer.grounded is True and answer.terminal_outcome.outcome == "answered"
    assert answer.metadata["lifecycle"]["deadline"]["policy"] == policy


async def test_verified_checkpoint_survives_timeout_with_identical_text_and_bindings():
    service, _provider, conversation, _repository, question = journey()
    service._chat_config.execution_policy = "bounded_v2"
    request = MessageSendRequest(content=question)
    answer = (await service.send_message(conversation.id, request)).assistant_message
    checkpoint = service._runner._verified_checkpoint
    assert checkpoint is not None
    result = service._runner.deadline_result(
        request, reason="request_deadline_exceeded", failure_phase="semantic_repair"
    )
    assert result.content == answer.content
    assert result.claims == checkpoint.restore().claims
    assert result.citations == checkpoint.restore().citations
    assert result.finalization.terminal.outcome == "partial"
    corrupted = checkpoint.model_copy(update={"payload_json": checkpoint.payload_json + " "})
    with pytest.raises(ValueError, match="identity"):
        corrupted.restore()


@pytest.mark.parametrize(
    "invalid", [None, "wrong_hash", "wrong_authority", "wrong_scope", "low_score"]
)
def test_semantic_confirmation_is_bounded_and_never_weakens_admission(invalid):
    from dataclasses import replace

    from app.core.config import ChatConfig
    from app.modules.conversations.services.evidence_repair_service import (
        _semantic_confirmation_candidates,
    )
    from app.platform.domain.content_hash import content_hash
    from app.platform.providers.request_work import RequestWork
    from tests.unit.modules.conversations.test_phase2_completion import source

    content = "প্রযোজ্য নিয়ম অনুযায়ী এই শর্ত পূরণ করতে হবে।"
    candidate = source(content)
    metadata = {
        "source_revision_id": str(uuid.uuid4()),
        "index_build_id": str(uuid.uuid4()),
        "source_metadata_generation": 7,
        "source_policy_applicable": True,
    }
    if invalid == "wrong_authority":
        metadata["authority_status"] = "unresolved"
    if invalid == "wrong_scope":
        metadata["source_policy_applicable"] = False
    candidate = replace(
        candidate,
        metadata=metadata,
        chunk_hash="bad" if invalid == "wrong_hash" else content_hash(content),
        rerank_relevance_score=0.1 if invalid == "low_score" else 0.95,
    )
    work = RequestWork(uuid.uuid4())
    candidates = [replace(candidate, chunk_id=uuid.uuid4()) for _ in range(3)]
    selected = _semantic_confirmation_candidates(
        "Which conditions apply?", candidates, [], ChatConfig(), work
    )
    assert len(selected) == (0 if invalid else 2)
    if invalid is None:
        assert (
            _semantic_confirmation_candidates(
                "Which conditions apply?", candidates, [], ChatConfig(), work
            )
            == []
        )
        assert all(c.rerank_relevance_score == 0.95 for c in selected)


@pytest.mark.parametrize(
    "claim,outcome",
    [
        ("The company must file within thirty days.", "scoped"),
        ("A director must file if no officers,", "scoped"),
        ("A director must file if officers are unwilling,", "unverifiable"),
    ],
)
def test_versioned_statutory_guard_reports_bounded_outcome(claim, outcome):
    from app.modules.conversations.grounding_service import _CLAUSE_NEGATION
    from app.modules.conversations.semantic_guards import evaluate_section_190

    evidence = "ব্যালান্স শীট রেজিষ্ট্রারের নিকট দাখিল ত্রিশদিন যদি কোম্পানীতে এইরূপ পদধারী কেহ না থাকেন"
    result = evaluate_section_190(claim, evidence, _CLAUSE_NEGATION)
    assert result.version == "companies.section190.v1"
    assert result.outcome == outcome
    assert (
        evaluate_section_190(claim, "Unrelated rule", _CLAUSE_NEGATION).outcome == "not_applicable"
    )


@pytest.mark.parametrize("invalid", [None, "status", "proof", "requirement", "shape"])
def test_complete_initial_draft_is_reused_only_with_approved_inventory(invalid):
    from types import SimpleNamespace

    from app.modules.conversations.services.message_execution_runner_service import (
        _complete_initial_assessment_draft,
    )

    draft = {"segments": [{"text": "A proved rule", "proof_ids": ["P"], "requirement_ids": ["R"]}]}
    if invalid == "proof":
        draft["segments"][0]["proof_ids"] = ["foreign"]
    if invalid == "requirement":
        draft["segments"][0]["requirement_ids"] = ["foreign"]
    if invalid == "shape":
        draft = {"segments": []}
    prepared = SimpleNamespace(
        retrieval_diagnostics={
            "knowledge_repair": {
                "status": "partial_answer" if invalid == "status" else "initial_evidence_complete",
                "initial_assessment": {"coverage_validated": True, "answer_draft": draft},
            }
        },
        selected=[
            SimpleNamespace(chunk_id="P", metadata={"reviewed_proof": [{"requirement_id": "R"}]})
        ],
        requirements=SimpleNamespace(requirements=[SimpleNamespace(requirement_id="R")]),
    )
    assert (_complete_initial_assessment_draft(prepared) is not None) is (invalid is None)


@pytest.mark.parametrize(
    "purpose,foreign_build,expected",
    [
        ("definition", False, "full"),
        ("structural", False, "partial"),
        ("definition", True, "partial"),
    ],
)
def test_legal_cross_section_dependency_is_not_a_table_dependency(purpose, foreign_build, expected):
    from dataclasses import replace

    from app.modules.conversations.execution_contracts import EvidenceRequirement
    from app.modules.conversations.services.evidence_coverage import CoverageVerdict
    from app.modules.conversations.services.evidence_repair_service import _guard_review_fulfillment
    from tests.unit.modules.conversations.test_phase2_completion import source

    first = source("A qualifying worker is a resident employee.")
    project = uuid.uuid4()
    metadata = {
        "project_id": str(project),
        "index_build_id": "build",
        "source_metadata_generation": 2,
        "source_revision_id": "edition",
    }
    first = replace(first, project_id=project, metadata={**metadata, "section_title": "Definition"})
    second = replace(
        source("A qualifying worker receives the allowance."),
        project_id=project,
        document_id=first.document_id,
        metadata={
            **metadata,
            "section_title": "Allowance",
            "index_build_id": "foreign" if foreign_build else "build",
        },
    )
    requirements = [
        EvidenceRequirement(
            requirement_id="D",
            description="Worker definition",
            materiality="central_rule",
            deliverable=False,
        ),
        EvidenceRequirement(
            requirement_id="R",
            description="Allowance rule",
            materiality="central_rule",
            depends_on=["D"],
            dependency_purposes={"D": purpose},
        ),
    ]
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": identity,
                    "supported": True,
                    "fulfillment": "full",
                    "evidence": [{"chunk_id": str(chunk.chunk_id), "quote": chunk.content}],
                }
                for identity, chunk in [("D", first), ("R", second)]
            ],
        }
    )
    result = _guard_review_fulfillment(verdict, requirements, [first, second])
    rule = next(check for check in result.checks if check.requirement_id == "R")
    assert rule.fulfillment == expected
    if expected == "full":
        assert {item.chunk_id for item in rule.evidence} == {
            str(first.chunk_id),
            str(second.chunk_id),
        }
