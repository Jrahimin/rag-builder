"""Saved AGM/comparison boundaries with fixed providers and a virtual delay clock.

No cancelled provider transcript was captured. Verdicts below reconstruct the saved
requirement/proof shape; elapsed work per requirement is an explicit counterfactual.
"""

import asyncio
import hashlib
import json
import time
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.services import (
    chat_service,
    claim_entailment_service,
    evidence_repair_service,
    message_execution_runner_service,
)
from app.modules.conversations.services.evidence_repair_service import repair_knowledge_evidence
from app.modules.conversations.terminal_outcome import terminal_outcome
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs, RequestScope
from app.platform.providers import request_work
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.request_work import ObservedLLM, RequestWork, current_request_purpose
from tests.unit.modules.conversations.test_evidence_repair_service import chunk
from tests.unit.modules.conversations.test_phase2_completion import execute_contract
from tests.unit.modules.conversations.test_post_qa_contract import captured_source, journey

pytestmark = pytest.mark.unit
FIXTURE = (
    Path(__file__).parents[3] / "fixtures/evaluation/saved_timeout_boundaries_20261104_v1.json"
)
FIXTURE_HASH = "d1126d08d7fdf6bf5c3eaf9df3e4eb90ac2dbe608208d096efa759226613ee16"
LATEST_FIXTURE = FIXTURE.with_name("saved_timeout_boundaries_20261104_v2.json")
LATEST_FIXTURE_HASH = "525850bdfc79b3adf363bea5576ed93220bdb5c56a78c9fd8e73ea7c18d3c275"


def saved_case(case):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_HASH
    return next(row for row in json.loads(raw)["cases"] if row["case"] == case)


def latest_saved_case(case):
    raw = LATEST_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LATEST_FIXTURE_HASH
    return next(row for row in json.loads(raw)["cases"] if row["case"] == case)


class DelayClock:
    """Advance recorded elapsed work; use native asyncio cancellation at the cutoff."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        self.active = []
        self.expirations = []
        self.native_timeout = asyncio.timeout
        proxy = SimpleNamespace(**{**vars(time), "perf_counter": lambda: self.now})
        monkeypatch.setattr(request_work, "time", proxy)
        monkeypatch.setattr(evidence_repair_service, "time", proxy)
        monkeypatch.setattr(claim_entailment_service, "time", proxy)
        monkeypatch.setattr(chat_service, "time", proxy)
        monkeypatch.setattr(message_execution_runner_service, "time", proxy)
        monkeypatch.setattr(evidence_repair_service, "monotonic", lambda: self.now)
        async_proxy = SimpleNamespace(**{**vars(asyncio), "timeout": self.timeout})
        monkeypatch.setattr(request_work, "asyncio", async_proxy)
        monkeypatch.setattr(evidence_repair_service, "asyncio", async_proxy)
        monkeypatch.setattr(claim_entailment_service, "asyncio", async_proxy)

    def timeout(self, seconds):
        clock = self

        class Scope:
            def __init__(self):
                self.deadline = None if seconds is None else clock.now + seconds
                self.native = clock.native_timeout(None)

            async def __aenter__(self):
                await self.native.__aenter__()
                clock.active.append(self)
                return self

            async def __aexit__(self, *args):
                clock.active.remove(self)
                return await self.native.__aexit__(*args)

            def expired(self):
                return self.native.expired()

        return Scope()

    async def delay(self, seconds):
        self.now += seconds
        expired = [s for s in self.active if s.deadline is not None and s.deadline <= self.now]
        if expired:
            # The outer shared evidence timer wins ties, as in the production path.
            scope = min(expired, key=lambda s: s.deadline)
            self.expirations.append(scope.deadline)
            scope.native.reschedule(asyncio.get_running_loop().time())
            await asyncio.Event().wait()
        await asyncio.sleep(0)


def reconstruction(case):
    """Use exact saved quoted strings, explicitly not a hidden full source capture."""
    bindings = {row["requirement_id"]: row["spans"] for row in case["evidence_bindings"]}
    quotes = list(dict.fromkeys(span["quote"] for spans in bindings.values() for span in spans))
    source_id = next(iter(bindings.values()))[0]["chunk_id"]
    source = replace(
        chunk("\n".join(quotes)),
        chunk_id=uuid.UUID(source_id),
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, f"saved-timeout-{case['case']}-source"),
        project_id=uuid.UUID("2ee2756f-ad27-44df-a9d3-1316b10ccbb1"),
        metadata={
            "index_build_id": case["scope"]["index_build_id"],
            "source_metadata_generation": 35,
        },
    )
    if case["case"] == 1:
        # A previous full-source capture has the exact admitted span hash in the
        # Nov 4 AGM response. Rebind its identity, never invent missing endings.
        captured = captured_source("4d0dd2f9-6902-495d-9c08-6dbdb536df1a")
        assert hashlib.sha256(captured.content.encode()).hexdigest() == (
            "13216c5c6ab5a1e686d0c458a0dd23024112e27104a2bcefe86bee99f9de7159"
        )
        assert all(quote in captured.content for quote in quotes)
        source = replace(source, content=captured.content, chunk_hash=captured.chunk_hash)
    checks = []
    by_id = {row["requirement_id"]: row for row in case["coverage"]["checks"]}
    for requirement in case["requirements"]:
        identity = requirement["requirement_id"]
        spans = bindings.get(identity, [])
        check = by_id[identity]
        checks.append(
            {
                "requirement_id": identity,
                "description": requirement["description"],
                "supported": bool(spans),
                "fulfillment": "full" if spans else "none",
                "answerable_scope": check["answerable_scope"] if spans else "",
                "evidence": deepcopy(spans),
            }
        )
    coverage = {
        "complete": False,
        "missing": case["initial_missing"],
        "checks": checks,
        "partial_answer": {
            "scope": "Independently evidenced source statements, with currentness unresolved.",
            "requirement_ids": list(bindings),
            "exclusions": case["initial_missing"],
        },
    }
    return source, coverage


class ReplayProvider:
    provider_name = "fixture"
    model_name = "fixed-saved-shape"
    provider_version = "1"

    def __init__(self, case, clock, coverage_delay=None):
        self.case, self.clock = case, clock
        self.source, self.coverage = reconstruction(case)
        self.coverage_delay = coverage_delay
        self.followup = False
        self.requests = []

    async def generate(self, messages, **kwargs):
        purpose = current_request_purpose()
        payload = json.loads(messages[1].content)
        self.requests.append((purpose, payload))
        if purpose == "recovery_planning":
            if self.followup and len(self.requests) > 1:
                return ChatCompletionResult(
                    content=json.dumps(
                        {
                            "queries": [
                                {
                                    "query": "current Companies Act subsequent amendment",
                                    "requirement_ids": ["R4"],
                                }
                            ]
                        }
                    ),
                    provider="fixture",
                    model=self.model_name,
                    provider_version="1",
                    finish_reason="stop",
                    usage=ChatUsage(1, 1),
                )
            recorded = next(c for c in self.case["provider_calls"] if c["purpose"] == purpose)
            await self.clock.delay(0.1 if self.followup else recorded["duration_ms"] / 1000)
            content = {
                "requirements": self.case["requirements"],
                "coverage": self.coverage,
                "queries": [
                    {"query": row["query"], "requirement_ids": row["requirement_ids"]}
                    for row in self.case["requirement_attempts"]
                ],
            }
        elif purpose == "coverage_review":
            ids = {r["requirement_id"] for r in payload["requirements"]}
            # Explicit service-demand model, NOT an inferred provider-internal timing.
            delay = self.coverage_delay
            if delay is None:
                total = next(
                    c["duration_ms"] for c in self.case["provider_calls"] if c["purpose"] == purpose
                )
                delay = total / 1000 * len(ids) / len(self.case["requirements"])
            await self.clock.delay(delay)
            content = {
                **self.coverage,
                "checks": [c for c in self.coverage["checks"] if c["requirement_id"] in ids],
            }
        else:
            content = {"queries": []}
        return ChatCompletionResult(
            content=json.dumps(content),
            provider="fixture",
            model=self.model_name,
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )


async def replay_recovery(
    monkeypatch, *, case_number=1, mutation=None, coverage_delay=None, followup=False
):
    case = saved_case(case_number)
    if mutation == "dependency":
        currentness = next(r for r in case["requirements"] if r["requirement_id"] == "R4")
        currentness["depends_on"] = ["R1"]
        currentness["dependency_purposes"] = {"R1": "applicability"}
    clock = DelayClock(monkeypatch)
    work = RequestWork(uuid.uuid4())
    work.configure_execution("legacy")
    work.deadline = work.started + 50.0  # ChatService's unchanged legacy entry policy.
    source, _ = reconstruction(case)
    provider = ReplayProvider(case, clock, coverage_delay)
    provider.followup = followup
    config = ChatConfig()
    grounding = GroundingService(config)
    decision = grounding.assess(case["question"], [source], rerank_status="off")
    source = next(iter(decision.admitted_units))
    branch_source = chunk("Independent discovery passage with no applicable current-force proof.")
    records = []
    if mutation == "content":
        branch_source = replace(source, content="Changed source with no reviewed proof.")
    elif mutation == "authority":
        records = [
            {
                "relationship_id": "new-affecting-edge",
                "base_document_id": str(source.document_id),
                "base_revision_id": str(source.document_id),
                "outcome": "unresolved",
                "relationship_type": "modifies",
                "provision_effect": "unknown",
            }
        ]
    elif mutation == "scope":
        branch_source = replace(
            source, metadata={**source.metadata, "source_effective_from": "2027-07-01"}
        )
    snapshot = {"index_build_id": case["scope"]["index_build_id"], "source_metadata_generation": 35}
    retrieval = AsyncMock()
    retrieval_count = 0

    async def retrieve(**kwargs):
        nonlocal retrieval_count
        retrieval_count += 1
        if followup:
            await clock.delay(0.1)
            return ContextRetrievalResult(
                chunks=[
                    chunk(
                        f"Independent discovery passage {retrieval_count} "
                        "without currentness proof."
                    )
                ],
                diagnostics=snapshot,
            )
        spans = [s for s in case["spans"] if s["name"] == "retrieval"]
        await clock.delay(spans[-1]["elapsed_ms"] / 1000)
        coverage_start = (
            next(s["start_offset_ms"] for s in case["spans"] if s["name"] == "coverage_review")
            / 1000
        )
        # Include saved embedding/setup gaps without summing nested spans.
        await clock.delay(coverage_start - (clock.now - work.started))
        return ContextRetrievalResult(
            chunks=[branch_source], diagnostics={**snapshot, "modifies_expansion_records": records}
        )

    retrieval.retrieve.side_effect = retrieve
    started = next(s["start_offset_ms"] for s in case["spans"] if s["name"] == "recovery_planning")
    clock.now += 0.1 if followup else started / 1000
    with work.attached():
        result = await repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(
                query=case["question"],
                normalized_scope=RequestScope.model_validate(case["scope"]),
                suppress_web=True,
            ),
            initial=ContextRetrievalResult(chunks=[source], diagnostics=snapshot),
            selected=[source],
            retrieval=retrieval,
            llm=ObservedLLM(provider, work),
            grounding=grounding,
            chat_config=config,
            retrieval_config=RetrievalConfig(),
            max_output_tokens=4096,
            timeout_seconds=30,
            max_initial_queries=3,
            max_followup_queries=int(followup),
            max_followup_rounds=int(followup),
            initial_decision=decision,
        )
    return result, provider, work, clock


async def test_saved_agm_first_recovery_reviews_only_unresolved_currentness(monkeypatch):
    result, provider, work, clock = await replay_recovery(monkeypatch)
    reviews = [payload for purpose, payload in provider.requests if purpose == "coverage_review"]
    assert len(reviews) == 1
    assert result.diagnostics.get("initial_partial_checkpoint") == "validated"
    assert [r["requirement_id"] for r in reviews[0]["requirements"]] == ["R4"]
    assert {r["requirement_id"] for r in reviews[0]["retained_supported"]} == {"R1", "R2", "R3"}
    assert result.decision is not None and result.decision.sufficient
    assert result.partial_answer["requirement_ids"] == ["R1", "R2", "R3"]
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False
    assert work.recovery_deadline == work.started + 30
    assert work.phase_deadline("claim_verification") == work.started + 50
    assert clock.now - work.started < 22
    assert work.counts["coverage_review_calls"] == 1
    assert [a["kind"] for a in result.diagnostics["recovery_actions"]] == ["search"]


async def test_initial_delta_preserves_the_existing_later_delta_allowance(monkeypatch):
    result, provider, work, _ = await replay_recovery(
        monkeypatch, coverage_delay=0.1, followup=True
    )
    reviews = [p for purpose, p in provider.requests if purpose == "coverage_review"]
    assert len(reviews) == 2
    assert all([r["requirement_id"] for r in p["requirements"]] == ["R4"] for p in reviews)
    actions = result.diagnostics["recovery_actions"]
    assert [a["kind"] for a in actions] == ["search", "search", "delta_review"]
    assert all(a["terminal_result"] == "completed" for a in actions)
    assert work.counts["coverage_review_calls"] == 2
    assert result.partial_answer["requirement_ids"] == ["R1", "R2", "R3"]
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False


@pytest.mark.parametrize("mutation", ["content", "authority", "scope", "dependency"])
async def test_changed_agm_proof_is_not_reused_by_first_review(monkeypatch, mutation):
    result, provider, _, _ = await replay_recovery(monkeypatch, mutation=mutation)
    reviews = [p for purpose, p in provider.requests if purpose == "coverage_review"]
    assert reviews
    assert {r["requirement_id"] for r in reviews[0]["requirements"]} == {"R1", "R2", "R3", "R4"}
    assert not reviews[0].get("retained_supported")
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False


async def test_slow_saved_comparison_review_still_expires_without_certified_answer(monkeypatch):
    result, provider, work, clock = await replay_recovery(
        monkeypatch, case_number=3, mutation="authority", coverage_delay=9
    )
    assert result.failure is not None, (
        clock.now - work.started,
        result.diagnostics,
        provider.requests,
    )
    assert result.diagnostics["stop_reason"] == "recovery_deadline_exceeded"
    assert result.decision is None and not result.selected
    assert any(purpose == "coverage_review" for purpose, _ in provider.requests)
    review = next(p for purpose, p in provider.requests if purpose == "coverage_review")
    assert {r["requirement_id"] for r in review["requirements"]} == {"R1", "R2", "R3", "R4", "R5"}
    assert not review.get("retained_supported")
    assert clock.expirations == [work.started + 30]
    assert work.calls[-1]["status"] == "cancelled"


async def test_slow_agm_delta_keeps_only_reviewed_evidence_not_publication(monkeypatch):
    result, _, work, clock = await replay_recovery(monkeypatch, coverage_delay=20)
    assert result.diagnostics.get("partial_checkpoint_restored") == "recovery_deadline_exceeded", (
        clock.now - work.started,
        clock.expirations,
        result.diagnostics,
    )
    assert result.partial_answer["requirement_ids"] == ["R1", "R2", "R3"]
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False
    assert not hasattr(
        result, "claims"
    )  # downstream assertion/publication verification remains mandatory
    assert clock.expirations == [work.started + 30]


async def test_changed_source_scope_cannot_restore_stale_partial_at_timeout(monkeypatch):
    result, _, _, _ = await replay_recovery(monkeypatch, mutation="scope", coverage_delay=20)
    assert result.failure is not None
    assert result.decision is None and not result.selected
    assert result.diagnostics["partial_checkpoint_invalidated"] == (
        "proof_content_requirement_or_authority_changed"
    )


async def replay_latest_boundary(monkeypatch, case_number, *, separate_instructions=None):
    """Recreate the observed stage boundary with explicit stand-in model replies."""
    case = latest_saved_case(case_number)
    scope = RequestScope.model_validate(saved_case(case_number)["scope"])
    assert (
        hashlib.sha256(scope.project_defaults.encode()).hexdigest()
        == case["project_defaults_sha256"]
    )
    clock = DelayClock(monkeypatch)
    work = RequestWork(uuid.uuid4())
    work.configure_execution("legacy")
    work.deadline = work.started + 50
    snapshot = {"index_build_id": scope.index_build_id, "source_metadata_generation": 35}
    config = ChatConfig()
    grounding = GroundingService(config)
    agm = case_number == 1
    if agm:
        source, _ = reconstruction(saved_case(1))
        sources = [source]
    else:
        # The later review recorded conflict selectors but not their full source
        # text. Use distinct same-period stand-ins, never certified tax evidence.
        sources = [
            chunk(f"AY 2026-27 resident-individual tax-free threshold is {amount}.")
            for amount in ("Tk 400,000", "Tk 500,000")
        ]
    decision = grounding.assess(case["question"], sources, rerank_status="off")
    requirements = case["requirements"]
    ids = [row["requirement_id"] for row in requirements]
    plans = (
        [
            {"query": "Companies Act current applicability", "requirement_ids": ["R4"]},
            {
                "query": "Companies Act general meeting timing",
                "requirement_ids": ["R1", "R2", "R3"],
            },
        ]
        if agm
        else [{"query": "AY 2025-26 resident tax threshold", "requirement_ids": ["T25"]}]
    )
    retrieval = AsyncMock()
    retrieval.supports_adjacent_retrieval = True
    retrieval.retrieve.return_value = ContextRetrievalResult(chunks=sources, diagnostics=snapshot)
    llm = AsyncMock(provider_name="fixture")
    system_prompts = []

    async def generate(messages, **kwargs):
        purpose = current_request_purpose()
        system_prompts.append((purpose, messages[0].content))
        if purpose == "recovery_planning":
            # AGM has zero validated bindings. The exact planner reply is unknown.
            payload = {"requirements": requirements, "queries": plans}
        elif purpose == "coverage_review":
            if agm:
                elapsed = next(
                    row["duration_ms"]
                    for row in case["provider_calls"]
                    if row["purpose"] == purpose
                )
                clock.now = work.started + 30 - elapsed / 1000
                await clock.delay(elapsed / 1000)
                raise AssertionError("The saved AGM coverage call was cancelled")
            # Place the completion before the cutoff, at the observed 808 ms reserve.
            budget = case["recovery_actions"][-1]["remaining_budget_seconds"]
            await clock.delay(work.started + 30 - budget - clock.now)
            payload = {
                "complete": False,
                "missing": [row["description"] for row in requirements],
                "checks": [
                    {
                        "requirement_id": row["requirement_id"],
                        "supported": row["supported"],
                        "fulfillment": row["fulfillment"],
                        "needs_adjacent_context": row["needs_adjacent_context"],
                        "evidence": [
                            {"chunk_id": str(source.chunk_id), "quote": source.content}
                            for source in sources
                        ]
                        if row["chunk_ids"]
                        else [],
                    }
                    for row in case["coverage_checks"]
                ],
            }
        else:
            raise AssertionError(f"Unexpected provider purpose {purpose}")
        return ChatCompletionResult(
            content=json.dumps(payload),
            provider="fixture",
            model="fixed",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    llm.generate.side_effect = generate
    with work.attached():
        result = await repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(query=case["question"], normalized_scope=scope),
            initial=ContextRetrievalResult(chunks=sources, diagnostics=snapshot),
            selected=sources,
            retrieval=retrieval,
            llm=llm,
            grounding=grounding,
            chat_config=config,
            retrieval_config=RetrievalConfig(),
            max_output_tokens=4096,
            timeout_seconds=30,
            max_initial_queries=3,
            max_followup_queries=0 if agm else 2,
            max_followup_rounds=0 if agm else 1,
            recovery_profile="focused" if agm else "broad",
            domain_instructions=(
                scope.project_defaults if separate_instructions is None else separate_instructions
            ),
            initial_decision=decision,
        )
    assert not result.selected and result.decision is None
    assert result.diagnostics["review_inputs"][0]["requirement_ids"] == ids
    return result, retrieval, system_prompts, scope


@pytest.mark.parametrize("case_number", [1, 3])
@pytest.mark.parametrize("separate", [None, "An independent trusted instruction."])
async def test_latest_recovery_prompt_carries_exact_project_defaults_once(
    monkeypatch, case_number, separate
):
    _, _, prompts, scope = await replay_latest_boundary(
        monkeypatch, case_number, separate_instructions=separate
    )
    assert [purpose for purpose, _ in prompts] == ["recovery_planning", "coverage_review"]
    for _, content in prompts:
        scope_line = content.split("Normalized request scope: ", 1)[1].split("\n", 1)[0]
        assert json.loads(scope_line) == scope.model_dump(mode="json")
        assert content.count("Trusted Project domain instructions:") == int(separate is not None)
        if separate is not None:
            assert separate in content


async def test_latest_comparison_budget_rejection_keeps_last_executed_stage(monkeypatch):
    result, retrieval, _, _ = await replay_latest_boundary(monkeypatch, 3)
    case = latest_saved_case(3)
    assert retrieval.retrieve.await_count == 1
    assert result.failure is None
    diagnostics = result.diagnostics
    assert diagnostics["coverage"]["full_coverage_validated"] is False
    assert diagnostics["stop_reason"] == "exhausted_budget"
    expected = case["recovery_actions"][1:]
    actual = diagnostics["recovery_actions"][1:]
    assert [{key: row[key] for key in expected[0]} for row in actual] == expected
    assert diagnostics["phase"] == "coverage_review"
    outcome = terminal_outcome(
        reason="unresolved_authority",
        supported_claims=0,
        partial=False,
        missing_inputs=[],
        diagnostics={"knowledge_repair": diagnostics},
        coverage="incomplete",
    )
    assert outcome.outcome == "timed_out"
    assert outcome.reason_code == "recovery_deadline_exceeded"
    assert outcome.failure_stage == "coverage"


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("first_support", [False, True])
async def test_saved_agm_verifier_cutoff_preserves_only_finalized_claims(
    monkeypatch, transport, first_support
):
    case = saved_case(1)
    clock = DelayClock(monkeypatch)
    service, provider, conversation, repository, _ = journey()
    _, coverage = reconstruction(case)
    for check in coverage["checks"]:
        for proof in check["evidence"]:
            proof["chunk_id"] = str(provider.source.chunk_id)
    provider.review = lambda: (case["requirements"], coverage)
    provider.answer = lambda: json.dumps(
        {
            "version": "answer.draft.v1",
            "segments": [
                {
                    "text": check["answerable_scope"],
                    "requirement_ids": [check["requirement_id"]],
                    "proof_ids": [str(provider.source.chunk_id)],
                }
                for check in coverage["checks"]
                if check["supported"]
            ],
            "notices": [
                {"kind": "unresolved_applicability", "proof_ids": [str(provider.source.chunk_id)]}
            ],
        }
    )
    original = provider.generate
    verifications = []

    async def generate(messages, **kwargs):
        purpose = current_request_purpose()
        if purpose == "claim_verification":
            assertions = json.loads(messages[1].content)
            verifications.append(assertions)
            if len(verifications) == 1:
                # Align at the captured first-verifier boundary; upstream provider
                # responses are fixed fixtures, not hidden captured transcripts.
                first = next(s for s in case["spans"] if s["name"] == "claim_verification")
                clock.now = service._work.started + first["start_offset_ms"] / 1000
                await clock.delay(
                    next(
                        c["duration_ms"] for c in case["provider_calls"] if c["purpose"] == purpose
                    )
                    / 1000
                )
            else:
                await clock.delay(9.0)  # crosses the unchanged verification ceiling
        result = await original(messages, **kwargs)
        if purpose == "claim_verification":
            payload = json.loads(result.content)
            for assertion, verdict in zip(assertions, payload["verdicts"], strict=True):
                supported = first_support and any(
                    p.get("requirement_id") == "R1" for p in assertion["proof"]
                )
                verdict.update(
                    status="supported" if supported else "unsupported",
                    failed_dimensions=[] if supported else ["scope"],
                    publication_complete=supported,
                )
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    answer = await execute_contract(
        service, provider, conversation, repository, case["question"], transport
    )
    assert len(verifications) == 2
    assert service._work.phase_deadline("claim_verification") == service._work.started + 50
    assert clock.expirations == [service._work.started + 50]
    assert "R4" in answer["terminal_outcome"]["unresolved_requirement_ids"]
    if first_support:
        assert "18 months" in answer["content"]
        assert "15 months" not in answer["content"]
        assert answer["claims"] and answer["citations"]
        assert all(c["verification"] == "supported" for c in answer["claims"])
        assert any(n["kind"] == "unresolved_applicability" for n in answer["notices"])
    else:
        assert not answer["claims"] and not answer["citations"]
        assert answer["finish_reason"] == "request_deadline_exceeded"
        assert not service._runner._verified_checkpoint
