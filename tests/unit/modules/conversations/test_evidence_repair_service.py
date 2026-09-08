"""Recovery must respect snapshot, scope, authority, and final context budgets."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import (
    _discovery_excerpts,
    _search_language_instruction,
    _source_hints,
    repair_knowledge_evidence,
)
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.errors import ProviderError

pytestmark = pytest.mark.unit


def test_source_hints_retain_languages_after_repeated_top_source():
    first = chunk("first", language="en")
    other = chunk("other", language="bn")
    hints = _source_hints([first] * 12 + [other])
    assert [hint["source"]["language"] for hint in hints] == ["en", "bn"]


def test_concept_language_prefers_current_governing_source_over_old_edition():
    hints = _source_hints(
        [
            chunk("old", language="en", source_role="primary", source_effective_from="2023-01-01"),
            chunk(
                "current", language="bn", source_role="primary", source_effective_from="2026-07-01"
            ),
        ]
    )
    assert "language code bn" in _search_language_instruction(hints)


def test_discovery_prioritizes_reviewed_gaps_without_starving_other_topics():
    noise = [chunk(f"Unrelated procedural passage {i}.") for i in range(8)]
    example = chunk("Example referring to the governing employment exclusion.")
    rebate = chunk("Investment eligibility requirements.")
    verdict = CoverageVerdict.model_validate(
        {
            "complete": False,
            "missing": ["employment exclusion", "investment eligibility"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": False,
                    "evidence": [{"chunk_id": str(example.chunk_id), "quote": example.content}],
                },
                {"query_index": 1, "supported": False, "evidence": []},
            ],
        }
    )
    excerpts = _discovery_excerpts(
        verdict, [[*noise, example], [rebate]], [*noise, example, rebate]
    )
    assert [item["content"] for item in excerpts[:2]] == [example.content, rebate.content]
    assert len(excerpts) == 8


def chunk(text: str, **metadata: object) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=1,
        content=text,
        score=0.95,
        semantic_score=0.95,
        filename="current guide.pdf",
        chunk_hash=content_hash(text),
        metadata=metadata,
    )


async def run_repair(
    branches,
    *,
    queries=None,
    config=None,
    initial_records=None,
    coverage=None,
    verification_finish="stop",
    verification_error=None,
    calls=None,
    selected_context=None,
    followup_queries=None,
    final_coverage=None,
    second_followup_queries=None,
    second_final_coverage=None,
    adjacent=False,
):
    config = config or ChatConfig()
    queries = (
        queries
        if queries is not None
        else ["gross salary exemption", "general rate bands", "current investment rebate"]
    )
    llm = AsyncMock()
    plan = ChatCompletionResult(
        content=json.dumps({"queries": queries}),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(12, 8),
        provider_version="1",
    )
    verdict = (
        coverage
        if coverage is not None
        else {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(items[0].chunk_id), "quote": items[0].content}],
                }
                for i, (items, _) in enumerate(branches)
                if items
            ],
        }
    )
    llm.generate.side_effect = [
        plan,
        verification_error
        or replace(
            plan,
            content=json.dumps(verdict),
            finish_reason=verification_finish,
        ),
    ]
    llm.generate.side_effect = [
        *llm.generate.side_effect,
        replace(
            plan, content=json.dumps({"queries": followup_queries or []}), usage=ChatUsage(0, 0)
        ),
        replace(plan, content=json.dumps(final_coverage or verdict)),
        replace(plan, content=json.dumps({"queries": second_followup_queries or []})),
        replace(plan, content=json.dumps(second_final_coverage or final_coverage or verdict)),
    ]
    retrieval = AsyncMock()
    if adjacent:
        retrieval.supports_adjacent_retrieval = True
        completions = list(llm.generate.side_effect)
        llm.generate.side_effect = [*completions[:2], *completions[3:]]
    snapshot = {"index_build_id": "build-a", "source_metadata_generation": 24}
    retrieval.retrieve.side_effect = [
        ContextRetrievalResult(chunks=items, diagnostics={**snapshot, **diagnostics})
        for items, diagnostics in branches
    ]
    inputs = EffectiveRetrievalInputs(
        query="Calculate from gross salary and eligible investment for this period.",
        document_id=uuid.uuid4(),
        metadata_filter={"region": "local"},
        as_of=datetime(2026, 7, 1, tzinfo=UTC),
        suppress_web=True,
    )
    result = await repair_knowledge_evidence(
        inputs=inputs,
        initial=ContextRetrievalResult(
            chunks=[],
            diagnostics={
                **snapshot,
                "modifies_expansion_records": initial_records or [],
            },
        ),
        selected=selected_context or [],
        retrieval=retrieval,
        llm=llm,
        grounding=GroundingService(config),
        chat_config=config,
        retrieval_config=RetrievalConfig(),
        max_output_tokens=1024,
    )
    if calls is not None:
        calls.extend(llm.generate.call_args_list)
    return result, retrieval, inputs


async def test_four_dependencies_preserve_source_authority_in_coverage_input():
    calls = []
    sources = [
        replace(
            chunk(
                f"Governing rule for dependency {i}.",
                source_role="supporting",
                source_type="official_guidance",
                source_effective_from="2026-07-01",
            ),
            page_number=20,
        )
        for i in range(4)
    ]
    result, retrieval, _ = await run_repair(
        [([source], {}) for source in sources],
        queries=[f"dependency {i}" for i in range(4)],
        calls=calls,
        selected_context=[
            chunk("Obsolete proposed forty-percent exemption", source_role="reference")
        ],
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 4
    planning_input = json.loads(calls[0].args[0][1].content)
    assert "Obsolete proposed forty-percent exemption" not in json.dumps(planning_input)
    assert planning_input["source_hints"][0]["source"]["source_role"] == "reference"
    coverage_input = json.loads(calls[1].args[0][1].content)
    assert all(
        item["source"]["source_role"] == "supporting"
        and item["source"]["source_type"] == "official_guidance"
        and item["source"]["source_effective_from"] == "2026-07-01"
        and item["page_number"] == 20
        and item["chunk_index"] == 1
        for item in coverage_input["context"]
    )


async def test_recovers_separate_salary_band_and_rebate_dependencies_with_original_scope():
    texts = [
        "Employment income exemption: one third of gross salary, capped at 500000.",
        "General rate bands for 2026: first 400000 nil, next 300000 ten percent.",
        "Current investment rebate is the lower of three percent of income, "
        "ten percent of investment and 750000.",
    ]
    result, retrieval, inputs = await run_repair([([chunk(text)], {}) for text in texts])
    assert result.diagnostics["status"] == "recovered"
    assert [item.content for item in result.selected] == texts
    assert result.decision is not None and result.decision.sufficient
    assert result.usage == ChatUsage(24, 16)
    for call in retrieval.retrieve.call_args_list:
        assert call.kwargs["document_id"] == inputs.document_id
        assert call.kwargs["metadata_filter"] == inputs.metadata_filter
        assert call.kwargs["as_of"] == inputs.as_of


async def test_coverage_review_serializes_uuid_authority_diagnostics_for_the_llm():
    authority_record = {
        "base_revision_id": uuid.uuid4(),
        "modifier_revision_id": uuid.uuid4(),
        "outcome": "ungoverned_or_incomplete_metadata",
        "target_provisions": [],
    }
    result, retrieval, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        initial_records=[authority_record],
    )
    assert result.diagnostics["status"] == "recovered"
    # The service would have raised before invoking the review provider if UUIDs
    # were not converted to JSON-safe identity strings.
    assert retrieval.retrieve.await_count == 1


@pytest.mark.parametrize(
    "change", [{"index_build_id": "build-b"}, {"source_metadata_generation": 25}]
)
async def test_does_not_mix_snapshots_during_repair(change):
    result, _, _ = await run_repair([([chunk("Gross salary exemption")], change)])
    assert result.diagnostics["status"] == "snapshot_changed"
    assert result.decision is None and not result.selected


async def test_old_rule_cannot_become_trusted_when_a_branch_omits_its_relationship():
    old = chunk("Current investment rebate is fifteen percent.", source_revision_id="old")
    result, _, _ = await run_repair(
        [([old], {})],
        queries=["current investment rebate"],
        initial_records=[
            {
                "base_revision_id": "old",
                "modifier_revision_id": "new",
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert not result.selected


async def test_orphan_table_never_repairs_a_dependency_despite_high_similarity():
    table = chunk("Rate bands: first 450000 nil", element_type="table")
    result, _, _ = await run_repair([([table], {})], queries=["general rate bands"])
    assert result.diagnostics["status"] == "coverage_incomplete"


async def test_later_branch_cannot_leave_an_earlier_unknown_rule_in_final_context():
    earlier = chunk("General rate bands", source_revision_id="old")
    result, _, _ = await run_repair(
        [
            ([earlier], {}),
            (
                [chunk("Current investment rebate")],
                {
                    "modifies_expansion_records": [
                        {
                            "base_revision_id": "old",
                            "modifier_revision_id": "new",
                            "outcome": "ungoverned_or_incomplete_metadata",
                            "target_provisions": [],
                        }
                    ]
                },
            ),
        ],
        queries=["general rate bands", "current investment rebate"],
    )
    assert result.diagnostics["status"] == "dependency_unresolved"
    assert result.decision is None and not result.selected


async def test_final_budget_must_retain_every_dependency():
    result, _, _ = await run_repair(
        [
            ([chunk(text)], {})
            for text in [
                "gross salary exemption",
                "general rate bands",
                "current investment rebate",
            ]
        ],
        config=ChatConfig(max_context_chunks=2),
    )
    assert result.diagnostics["status"] == "dependency_exceeds_budget"
    assert not result.selected


@pytest.mark.parametrize("queries", [[], [" "], ["q"] * 9, ["q" * 501]])
async def test_invalid_plans_never_search_or_authorize_generation(queries):
    result, retrieval, _ = await run_repair([], queries=queries)
    assert result.decision is None
    retrieval.retrieve.assert_not_called()


async def test_non_tax_policy_recovery_uses_the_same_path():
    result, _, _ = await run_repair(
        [([chunk("Enterprise renewal charges are waived for eligible customers.")], {})],
        queries=["Enterprise renewal eligibility and charges"],
    )
    assert result.diagnostics["status"] == "recovered"


async def test_relevant_definitions_and_unlinked_old_translation_do_not_prove_coverage():
    branches = [
        ([chunk("Employment income means salary and benefits.")], {}),
        ([chunk("File returns with the applicable city corporation office.")], {}),
        (
            [
                chunk(
                    "Section 78: fifteen percent of investment, capped at one million.",
                    source_revision_id="english-act",
                )
            ],
            {},
        ),
    ]
    result, _, _ = await run_repair(
        branches,
        initial_records=[
            {
                "base_revision_id": "bangla-act",
                "modifier_revision_id": "new",
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
        coverage={
            "complete": False,
            "missing": ["Applicable period, exemption and amended rebate"],
            "checks": [{"query_index": i, "supported": False, "evidence": []} for i in range(3)],
        },
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert result.decision is None and not result.selected
    assert result.usage == ChatUsage(24, 16)


@pytest.mark.parametrize("fault", ["invented_quote", "foreign_id", "missing_check", "missing_rule"])
async def test_verifier_cannot_authorize_unbound_or_incomplete_evidence(fault):
    source = chunk("Eligible refunds must be requested within 45 days.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
            }
        ],
    }
    if fault == "invented_quote":
        verdict["checks"][0]["evidence"][0]["quote"] = "Refunds are unlimited."
    elif fault == "foreign_id":
        verdict["checks"][0]["evidence"][0]["chunk_id"] = str(uuid.uuid4())
    elif fault == "missing_check":
        verdict["checks"] = []
    else:
        verdict["missing"] = ["Customer eligibility"]
    result, _, _ = await run_repair([([source], {})], queries=["refund terms"], coverage=verdict)
    assert result.diagnostics["status"] == (
        "repair_unavailable"
        if fault in {"missing_check", "missing_rule"}
        else "coverage_incomplete"
    )
    assert result.decision is None and not result.selected


async def test_truncated_positive_verdict_cannot_enable_generation():
    result, _, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        verification_finish="length",
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert result.decision is None and not result.selected


async def test_final_admitted_evidence_can_support_another_search_dependency():
    terms = chunk("Refunds require a receipt and are limited to 45 days.")
    receipt = chunk("Receipt records identify the purchase.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": i,
                "supported": True,
                "evidence": [{"chunk_id": str(terms.chunk_id), "quote": terms.content}],
            }
            for i in range(2)
        ],
    }
    result, _, _ = await run_repair(
        [([terms], {}), ([receipt], {})],
        queries=["refund time limit", "required receipt"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert [c.chunk_id for c in result.selected] == [terms.chunk_id]
    assert result.diagnostics["proof_chunk_ids"] == [str(terms.chunk_id)]


async def test_rule_below_four_relevant_candidates_survives_for_completeness_review():
    sources = [chunk(f"Refund policy background item {i}.") for i in range(4)]
    rule = chunk("The governing refund period is 45 days.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(rule.chunk_id), "quote": rule.content}],
            }
        ],
    }
    result, _, _ = await run_repair(
        [([*sources, rule], {})],
        queries=["refund period"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert rule.chunk_id in {c.chunk_id for c in result.selected}
    assert len(result.selected) == 1
    assert {unit.chunk_id for unit in result.decision.admitted_units} == {rule.chunk_id}


@pytest.mark.parametrize("label", ["E1", "E99"])
async def test_short_passage_labels_bind_only_to_the_final_context(label):
    source = chunk("Refunds are available for 45 days.")
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": label, "quote": source.content}],
            }
        ],
    }
    result, _, _ = await run_repair([([source], {})], queries=["refunds"], coverage=coverage)
    assert (result.diagnostics["status"] == "recovered") is (label == "E1")
    if label == "E1":
        assert result.diagnostics["coverage"]["checks"][0]["chunk_ids"] == [str(source.chunk_id)]


@pytest.mark.parametrize(
    ("quote", "accepted"),
    [
        ("দফা (৩৬)-তে আয় বাদ। Rate: 10%.", True),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 15%.", False),
        ("দফা (৩৬)-তে আয় বাদ নয়। Rate: 10%.", False),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 1 0%.", False),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 10.", False),
    ],
)
async def test_ocr_quote_spacing_preserves_all_words_numbers_and_punctuation(quote, accepted):
    source = chunk("দফা ( ৩৬ ) -তে\n\nআয় বাদ । Rate : 10 % .")
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": quote}],
            }
        ],
    }
    result, _, _ = await run_repair([([source], {})], queries=["Rate"], coverage=coverage)
    assert (result.diagnostics["status"] == "recovered") is accepted


@pytest.mark.parametrize(
    "error", [TimeoutError(), ProviderError("unavailable", provider_name="fake")]
)
async def test_verification_failure_preserves_failure_and_unknown_usage(error):
    result, _, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        verification_error=error,
    )
    assert result.diagnostics["status"] == "repair_unavailable"
    assert result.decision is None and not result.selected
    assert result.usage == ChatUsage(None, None)


@pytest.mark.parametrize(
    "missing", ["Employment income exclusion", "Applicable minimum tax period"]
)
async def test_focused_followup_recovers_missing_rule_without_discarding_initial_evidence(missing):
    first = chunk("Current rate bands apply to resident individuals.")
    focused = chunk("Current governing exclusion and minimum-tax conditions.")

    def check(i, source):
        return {
            "query_index": i,
            "supported": True,
            "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
        }

    calls = []
    result, retrieval, inputs = await run_repair(
        [([first], {}), ([focused], {})],
        queries=["current rates"],
        coverage={"complete": False, "missing": [missing], "checks": [check(0, first)]},
        followup_queries=["focused governing rule"],
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [check(0, first), check(1, focused)],
        },
        calls=calls,
    )
    assert result.diagnostics["status"] == "recovered"
    assert {c.chunk_id for c in result.selected} == {first.chunk_id, focused.chunk_id}
    assert json.loads(calls[2].args[0][1].content)["missing_requirements"] == [missing]
    assert result.diagnostics["initial_coverage"]["complete"] is False
    assert retrieval.retrieve.await_count == 2
    assert retrieval.retrieve.call_args.kwargs["as_of"] == inputs.as_of
    assert result.usage == ChatUsage(36, 24)


async def test_focused_followup_rejects_snapshot_change():
    first = chunk("Current governing rates.")
    result, _, _ = await run_repair(
        [([first], {}), ([chunk("Exclusion rule")], {"index_build_id": "different"})],
        queries=["rates"],
        followup_queries=["exclusion"],
        coverage={"complete": False, "missing": ["exclusion"], "checks": []},
    )
    assert result.diagnostics["status"] == "snapshot_changed"
    assert not result.selected


async def test_focused_followup_stops_when_no_new_alternative_is_available():
    result, retrieval, _ = await run_repair(
        [([chunk("Current rates")], {}), ([chunk("Related definitions")], {})],
        queries=["rates"],
        followup_queries=["exclusion"],
        coverage={"complete": False, "missing": ["exclusion"], "checks": []},
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert retrieval.retrieve.await_count == 2
    assert not result.selected


async def test_focused_discovery_uses_missing_branch_excerpts_without_promoting_them():
    example = chunk("Worked example applies a one-third exclusion; see Schedule Six.")
    rule = chunk("The governing exclusion is one third, subject to the stated cap.")
    calls = []
    result, _, _ = await run_repair(
        [([example], {}), ([rule], {})],
        queries=["verbose unsuccessful employment search"],
        coverage={
            "complete": False,
            "missing": ["Governing exclusion"],
            "checks": [{"query_index": 0, "supported": False, "evidence": []}],
        },
        followup_queries=["employment exclusion schedule"],
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "quote": rule.content}],
                }
                for i in range(2)
            ],
        },
        calls=calls,
    )
    followup = json.loads(calls[2].args[0][1].content)
    assert followup["discovery_excerpts"] == [
        {"title": example.filename, "content": example.content}
    ]
    assert [c.chunk_id for c in result.selected] == [rule.chunk_id]
    assert "Worked example" not in json.dumps(result.diagnostics)


@pytest.mark.parametrize("first_response", ['```json\n{"queries":["rule"]}\n```', "not JSON"])
async def test_json_format_recovery_remains_schema_validated(first_response):
    from app.modules.conversations.services.evidence_repair_service import (
        _SearchPlan,
        _validated_completion,
    )

    base = ChatCompletionResult(first_response, "fake", "test", "stop", ChatUsage(2, 3), "1")
    llm = AsyncMock()
    llm.generate.side_effect = [base, replace(base, content='{"queries":["rule"]}')]
    response = await _validated_completion(llm, [], schema=_SearchPlan, max_tokens=1024)
    assert _SearchPlan.model_validate_json(response.content).queries == ["rule"]
    expected_calls = 1 if first_response.startswith("```") else 2
    assert llm.generate.await_count == expected_calls
    assert response.usage == ChatUsage(2 * expected_calls, 3 * expected_calls)


async def test_empty_search_route_can_be_proved_by_another_route():
    rule = chunk("The governing employment exclusion applies to ordinary employees.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": i,
                "supported": True,
                "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 1}],
            }
            for i in range(2)
        ],
    }
    result, retrieval, _ = await run_repair(
        [([], {}), ([rule], {})],
        queries=["unsuccessful wording", "governing wording"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 2
    assert [c.chunk_id for c in result.selected] == [rule.chunk_id]


async def test_second_focused_pass_is_bounded_and_can_resolve_remaining_gap():
    example = chunk("Worked example")
    rule = chunk("Complete governing rule")
    incomplete = {"complete": False, "missing": ["governing rule"], "checks": []}
    result, retrieval, _ = await run_repair(
        [([example], {}), ([], {}), ([rule], {})],
        queries=["initial search"],
        coverage=incomplete,
        followup_queries=["first alternative"],
        final_coverage=incomplete,
        second_followup_queries=["second alternative"],
        second_final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 1}],
                }
                for i in range(3)
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 3
    assert result.diagnostics["focused_queries"] == ["first alternative", "second alternative"]


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 99)])
async def test_invalid_source_ranges_cannot_authorize_answer(start, end):
    rule = chunk("A rule with punctuation: no invented dash.\nThe rate is 10%.")
    result, _, _ = await run_repair(
        [([rule], {})],
        queries=["rate"],
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(rule.chunk_id), "start_line": start, "end_line": end}
                    ],
                }
            ],
        },
    )
    assert not result.selected


async def test_source_range_materialization_preserves_punctuation_and_numbers():
    rule = chunk("Scope: ordinary employees.\n\nThe rate is 10%, not 15%.")
    calls = []
    result, _, _ = await run_repair(
        [([rule], {})],
        queries=["rate"],
        calls=calls,
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 3}],
                }
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert result.selected[0].content == rule.content
    assert json.loads(calls[1].args[0][1].content)["context"][0]["content"].startswith("L1: Scope:")


async def test_adjacent_recovery_keeps_scope_and_rechecks_original_requirements():
    continuation = chunk("Continuation without governing heading.")
    governing = chunk("The applicable rate is 10% for the current period.")
    calls = []
    result, retrieval, inputs = await run_repair(
        [([continuation], {}), ([governing], {})],
        queries=["Discovery wording mentioning unrelated future years"],
        coverage={
            "complete": False,
            "missing": ["governing heading"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": False,
                    "needs_adjacent_context": True,
                    "evidence": [
                        {"chunk_id": str(continuation.chunk_id), "start_line": 1, "end_line": 1}
                    ],
                },
            ],
        },
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(governing.chunk_id), "start_line": 1, "end_line": 1}
                    ],
                }
                for i in range(2)
            ],
        },
        adjacent=True,
        calls=calls,
    )
    assert result.diagnostics["status"] == "recovered"
    assert len(calls) == 3  # no extra planning call for deterministic neighbours
    request = retrieval.retrieve.call_args_list[1].kwargs
    assert request["adjacent_to"] == [continuation.chunk_id]
    assert request["document_id"] == inputs.document_id
    assert request["metadata_filter"] == inputs.metadata_filter
    assert request["as_of"] == inputs.as_of
    review = json.loads(calls[1].args[0][1].content)
    assert "unrelated future years" not in json.dumps(review)
    assert review["original_question"] == inputs.query


async def test_contradictory_complete_verdict_gets_one_format_retry():
    from app.modules.conversations.services.evidence_repair_service import _validated_completion

    contradictory = {
        "complete": True,
        "missing": [],
        "checks": [{"query_index": 0, "supported": False, "evidence": []}],
    }
    corrected = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": "E1", "start_line": 1, "end_line": 2}],
            }
        ],
    }
    llm = AsyncMock()
    llm.generate.side_effect = [
        ChatCompletionResult(
            content=json.dumps(value),
            provider="fake",
            provider_version="1",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )
        for value in (contradictory, corrected)
    ]
    result = await _validated_completion(llm, [], schema=CoverageVerdict, max_tokens=1024)
    assert json.loads(result.content) == corrected
    assert llm.generate.await_count == 2
    assert "every check" in llm.generate.call_args.args[0][-1].content
    # The correction still needs exact source validation; JSON consistency alone
    # cannot authorize generation from invented line ranges.
    assert not CoverageVerdict.model_validate_json(result.content).resolve_source_ranges([])


async def test_followup_carries_confirmed_proof_without_old_search_noise():
    known = chunk("The governing rate is 10%.")
    example = chunk("An illustrative example, not the missing governing exclusion.")
    noise = chunk("Unrelated administrative procedure.")
    exclusion = chunk("The governing exclusion applies to ordinary employees.")
    calls = []
    initial_check = {
        "query_index": 0,
        "supported": True,
        "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
    }
    result, _, _ = await run_repair(
        [([known, noise], {}), ([example], {}), ([exclusion], {})],
        queries=["rate", "employment exclusion"],
        config=ChatConfig(max_context_chunks=3),
        coverage={
            "complete": False,
            "missing": ["governing exclusion"],
            "checks": [initial_check, {"query_index": 1, "supported": False, "evidence": []}],
        },
        followup_queries=["alternative exclusion search"],
        calls=calls,
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                initial_check,
                *[
                    {
                        "query_index": i,
                        "supported": True,
                        "evidence": [
                            {"chunk_id": str(exclusion.chunk_id), "quote": exclusion.content}
                        ],
                    }
                    for i in (1, 2)
                ],
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    context = json.loads(calls[3].args[0][1].content)["context"]
    assert {item["content"] for item in context} == {
        "L1: " + known.content,
        "L1: " + exclusion.content,
    }
