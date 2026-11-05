"""Replay the two saved cancellations without guessing their provider responses."""

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.services import evidence_repair_service as repair
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs, RequestScope
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.request_work import ObservedLLM, RequestWork, current_request_purpose
from tests.unit.modules.conversations.test_evidence_repair_service import chunk
from tests.unit.modules.conversations.test_saved_timeout_replay import DelayClock, saved_case

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[3] / "fixtures/evaluation/saved_coverage_cutoffs_20261104.json"


@pytest.mark.parametrize("case_number", [1, 3])
async def test_saved_coverage_cutoff_sends_only_proof_relevant_wire_data(monkeypatch, case_number):
    case = next(
        row for row in json.loads(FIXTURE.read_bytes())["cases"] if row["case"] == case_number
    )
    defaults = saved_case(case_number)["scope"]["project_defaults"]
    assert hashlib.sha256(defaults.encode()).hexdigest() == case["project_defaults_sha256"]
    scope = RequestScope.model_validate({**case["scope"], "project_defaults": defaults})
    records = case["initial_authority_records"]
    duplicate = {**records[0], "candidate_count": records[0]["candidate_count"] + 1}
    changed = {**duplicate, "modifier_effective_from": "2000-01-01", "outcome": "unresolved"}
    raw_records = deepcopy([*records, duplicate, changed])
    snapshot = {"index_build_id": scope.index_build_id, "source_metadata_generation": 35}

    # The saved responses identify the passage inventory, but not the proof text
    # delivered to the provider. These inert stand-ins stress multiline selectors.
    chunk_ids = dict.fromkeys(
        case["review_chunks"] + [i for q in case["queries"] for i in q["selected_chunk_ids"]]
    )
    chunks = {
        identity: replace(
            chunk(
                f"Fixture header {i}\n\n" + "\n".join(f"Fixture line {j}." for j in range(20)),
                source_revision_id=records[i % len(records)]["modifier_revision_id"],
                source_effective_from="2025-07-01",
            ),
            chunk_id=uuid.UUID(identity),
            document_id=uuid.uuid5(uuid.NAMESPACE_URL, identity),
            filename=f"Fixture source {i}",
        )
        for i, identity in enumerate(chunk_ids)
    }
    initial = [chunks[i] for i in case["initial_chunks"]]
    config = ChatConfig(max_context_chunks=16, context_char_budget=24000)
    grounding = GroundingService(config)
    decision = grounding.assess(scope.subject, initial, rerank_status="off")

    async def admit(**kwargs):
        return replace(decision, sufficient=True, reason=None), kwargs["chunks"]

    monkeypatch.setattr(repair, "assess_and_select_knowledge", admit)
    clock = DelayClock(monkeypatch)
    work = RequestWork(uuid.uuid4())
    work.configure_execution("legacy")
    work.deadline = work.started + 50
    spans = {span["name"]: span for span in case["spans"]}
    clock.now = work.started + spans["recovery_planning"]["start_offset_ms"] / 1000
    requests = {}

    async def generate(messages, **kwargs):
        purpose = current_request_purpose()
        requests[purpose] = messages
        if purpose == "recovery_planning":
            await clock.delay(spans[purpose]["elapsed_ms"] / 1000)
            payload = {
                "queries": [
                    {k: q[k] for k in ("query", "requirement_ids")} for q in case["queries"]
                ],
                "requirements": case["requirements"],
            }
            return ChatCompletionResult(
                content=json.dumps(payload),
                provider="fixture",
                model="fixture",
                provider_version="1",
                finish_reason="stop",
                usage=ChatUsage(1, 1),
            )
        assert purpose == "coverage_review"
        clock.now = work.started + spans[purpose]["start_offset_ms"] / 1000
        await clock.delay(spans[purpose]["elapsed_ms"] / 1000)
        pytest.fail("The saved coverage boundary must cancel before an invented response")

    provider = AsyncMock(provider_name="fixture", model_name="fixture", provider_version="1")
    provider.request_provenance = None
    provider.generate.side_effect = generate
    retrieval = AsyncMock()
    retrieval.supports_batch_retrieval = True

    async def retrieve_batch(requests, **kwargs):
        groups = {q["query"]: q for q in case["queries"]}
        return [
            ContextRetrievalResult(
                chunks=[chunks[i] for i in groups[r["query"]]["selected_chunk_ids"]],
                diagnostics={**snapshot, "modifies_expansion_records": [duplicate, changed]},
            )
            for r in requests
        ]

    retrieval.retrieve_batch.side_effect = retrieve_batch
    with work.attached():
        result = await repair.repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(query=scope.subject, normalized_scope=scope),
            initial=ContextRetrievalResult(
                chunks=initial,
                diagnostics={**snapshot, "modifies_expansion_records": records},
            ),
            selected=initial,
            retrieval=retrieval,
            llm=ObservedLLM(provider, work),
            grounding=grounding,
            chat_config=config,
            retrieval_config=RetrievalConfig(),
            max_output_tokens=4096,
            timeout_seconds=30,
            initial_decision=decision,
        )

    assert list(requests) == ["recovery_planning", "coverage_review"]
    planning, coverage = (json.loads(messages[1].content) for messages in requests.values())
    print(
        json.dumps(
            {
                "case": case_number,
                "message_id": case["message_id"],
                "wire_utf8_bytes": {
                    purpose: [len(m.content.encode()) for m in messages]
                    for purpose, messages in requests.items()
                },
                "planner_passages": len(planning["admitted_evidence"]),
                "review_passages": len(coverage["context"]),
                "authority_wire_records": len(coverage["authority_limitations"]),
            }
        )
    )
    assert len(planning["admitted_evidence"]) == len(initial)
    assert len(coverage["context"]) == len(case["review_chunks"])
    review_ids = result.diagnostics["review_inputs"][0]["chunk_ids"]
    assert set(review_ids) == set(case["review_chunks"])
    for i, (record, identity) in enumerate(zip(coverage["context"], review_ids, strict=True), 1):
        assert record["chunk_id"] == f"E{i}"
        assert record["title"] == chunks[identity].filename
        assert record["source"] == chunks[identity].metadata
        assert record["source_revision_id"] == chunks[identity].metadata["source_revision_id"]
    for record in planning["admitted_evidence"]:
        source = initial[int(record["chunk_id"][1:]) - 1]
        assert record["content"].splitlines() == [
            f"L{i}: {line}" for i, line in enumerate(source.content.splitlines(), 1)
        ]
        assert "source_lines" not in record
    coverage_messages = requests["coverage_review"]
    retry = repair._structured_selector_retry(
        requests["recovery_planning"],
        initial,
        {f"E{i}": str(c.chunk_id) for i, c in enumerate(initial, 1)},
    )
    retry_passages = json.loads(retry[1].content)["admitted_evidence"]
    assert all(r["source_lines"][1]["start_line"] == 3 for r in retry_passages)
    assert "Bounded recovery allowance" in requests["recovery_planning"][0].content
    assert "Bounded recovery allowance" not in coverage_messages[0].content
    assert coverage_messages[0].content.count("Omitting a gap or replacing") == 1
    scope_line = coverage_messages[0].content.split("\n", 1)[0]
    assert json.loads(scope_line.removeprefix("Normalized request scope: ")) == scope.model_dump(
        mode="json"
    )
    expected_requirements = deepcopy(case["requirements"])
    if case_number == 3:
        # A year-specific requirement carries only its own period; the
        # subtraction retains the canonical two-year scope above.
        for row in expected_requirements:
            if row["requirement_id"] in {"A25", "T25", "A26", "T26"}:
                start = 2025 if row["requirement_id"].endswith("25") else 2026
                row["assigned_scope"] = {
                    "requested_periods": [
                        period
                        for period in scope.model_dump(mode="json")["requested_periods"]
                        if period["start_year"] == start
                    ]
                }
    assert coverage["requirements"] == expected_requirements
    assert "discovery_routes" not in coverage
    expected_authority = [
        {k: v for k, v in r.items() if k not in {"candidate_count", "retained_candidate_count"}}
        for r in [*records, changed]
    ]
    assert coverage["authority_limitations"] == expected_authority
    assert [*records, duplicate, changed] == raw_records
    assert {
        branch["query"]: (branch["requirement_ids"], branch["selected_chunk_ids"])
        for branch in result.diagnostics["branches"]
    } == {q["query"]: (q["requirement_ids"], q["selected_chunk_ids"]) for q in case["queries"]}
    assert work.recovery_deadline == work.started + 30
    assert clock.expirations == [work.recovery_deadline]
    assert result.diagnostics["phase"] == "coverage_review"
    assert result.diagnostics["stop_reason"] == "recovery_deadline_exceeded"
    assert result.decision is None and not result.selected
