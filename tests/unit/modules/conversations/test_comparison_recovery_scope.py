"""Comparison recovery keeps independent periods and exact proof identities."""

import asyncio
import json
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import (
    EvidenceRequirement,
    _guard_review_fulfillment,
    _review_requirements_payload,
)
from app.modules.conversations.services.message_execution_runner_service import (
    _calculation_operands,
    _verified_comparison_pairs,
)
from app.modules.conversations.turn_resolution import normalize_request_scope
from tests.unit.modules.conversations.test_evidence_repair_service import (
    _coverage_review_payloads,
    chunk,
    run_repair,
)

pytestmark = pytest.mark.unit
QUESTION = "Compare the allowance rate for AY 2024-25 and AY 2025-26 using each year's source."


@pytest.mark.parametrize(
    ("use_wrong_band", "same_snapshot", "recover", "cross_page"),
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (True, True, False, False),
        (True, True, True, False),
        (False, True, False, True),
        (True, True, True, True),
    ],
)
async def test_saved_circular_coverage_binds_each_threshold_before_comparing(
    use_wrong_band, same_snapshot, recover, cross_page
):
    question = "Compare the resident-individual thresholds in AY 2025-26 and AY 2026-27."
    project = uuid.uuid4()
    snapshot = {"index_build_id": "build-a", "source_metadata_generation": 24}
    # Operative lines and the letter-spaced running footer from the saved QA evidence.
    heading = (
        "অর্থ আইন , ২০২৪ এ বর্ণিত করহারের তফসিল -৩ অনুযায়ী প্রত্যেক স্বাভাবিক ব্যক্তি\n\n"
        "( বাংলাদেশী নন এরূপ বাংলাদেশে অনিবাসী স্বাভাবিক ব্যক্তি ব্যতীত ) , হিন্দু অবিভক্ত\n\n"
        "পরিবার ও ফার্মের মোট আয়ের উপর ২০২৫-২০২৬ করবর্ষের জন্য আয়করের হার\n\nনিম্নরূপ :"
    )
    prior = replace(
        chunk(
            heading + "\nমোট আয় | হার\n( ক ) প্রথম ৩,৫০,০০০ টাকা পর্যন্ত মোট আয়ের উপর -- | শূন্য",  # noqa: RUF001
            **snapshot,
        ),
        project_id=project,
        chunk_index=34,
    )
    wrong = replace(
        chunk(
            "অর্থ অধ্যাদেশ , ২০২৫ এ বর্ণিত করহারের তফসিল -২ ও তফসিল -৩ অনুযায়ী প্রত্যেক\n"
            "আ য় ক র প রি প ত্র 2025-2026 | 1\nমোট আয় | হার\n"
            "( ক ) প্রথম ৩,৭৫,০০০ টাকা পর্যন্ত মোট আয়ের উপর - | শূন্য",  # noqa: RUF001
            **snapshot,
        ),
        project_id=project,
        document_id=prior.document_id,
        chunk_index=26,
    )
    later = replace(
        chunk(
            "২০২৬-২০২৭ এবং ২০২৭-২০২৮ করবর্ষের জন্য করহার\nকরধাপ | করহার\n৪,০০,০০০ টাকা পর্যন্ত | শূন্য",  # noqa: RUF001
            **snapshot,
        ),
        project_id=project if same_snapshot else uuid.uuid4(),
    )
    future = replace(
        chunk(
            "সকল নিবাসী স্বাভাবিক ব্যক্তি ও হিন্দু অবিভক্ত পরিবারের মোট আয়ের উপর আয়করের\n"
            "হার নিম্নরূপ :\nমোট আয় করহার\nপ্রথম ৫,০০,০০০ টাকা পর্যন্ত শূন্য\n"  # noqa: RUF001
            "আয়কর পরিপত্র ২০২৬-২০২৭ ।৪",  # noqa: RUF001
            **snapshot,
        ),
        project_id=project,
        page_number=17,
        chunk_index=35,
    )
    preceding_pages = {
        wrong.chunk_id: (
            "১.১ স্বাভাবিক ব্যক্তি ও হিন্দু অবিভক্ত পরিবারের ২০২৬-২০২৭ ও ২০২৭-২০২৮ করবর্ষের\n"
            "জন্য করহার\nঅর্থ অধ্যাদেশ , ২০২৫ এ বর্ণিত করহারের তফসিল -২ ও তফসিল -৩ অনুযায়ী"
        ),
        future.chunk_id: (
            "১.৩ স্বাভাবিক ব্যক্তি ( individual ) ও হিন্দু অবিভক্ত পরিবারের ২০৩০-২০৩১ করবর্ষের\n"
            "জন্য করহার\nঅর্থ আইন , ২০২৬ এ বর্ণিত করহারের তফসিল -২ অনুযায়ী অনিবাসী বাংলাদেশিসহ"
        ),
    }
    requirements = [
        {
            "requirement_id": "scope",
            "description": "Applicable resident-individual schedule for AY 2025-26",
            "materiality": "governing_applicability",
        },
        {
            "requirement_id": "early",
            "description": "Resident-individual threshold for AY 2025-26",
            "depends_on": ["scope"],
            "dependency_purposes": {"scope": "applicability"},
        },
        {
            "requirement_id": "late_scope",
            "description": "Applicable resident-individual schedule for AY 2026-27",
            "materiality": "governing_applicability",
        },
        {
            "requirement_id": "late",
            "description": "Resident-individual threshold for AY 2026-27",
            "depends_on": ["late_scope"],
            "dependency_purposes": {"late_scope": "applicability"},
        },
        {
            "requirement_id": "delta",
            "description": "Difference between the two thresholds",
            "task_kind": "comparison",
            "depends_on": ["early", "late"],
            "dependency_purposes": {"early": "calculation_operand", "late": "calculation_operand"},
        },
    ]

    def span(source, quote=None):
        return {"chunk_id": str(source.chunk_id), "quote": quote or source.content}

    early = wrong if use_wrong_band else prior
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {"requirement_id": req, "supported": True, "fulfillment": "full", "evidence": proof}
            for req, proof in (
                ("scope", [span(prior, heading)]),
                ("early", [span(early), span(prior, heading)]),
                ("late_scope", [span(later, later.content.splitlines()[0])]),
                ("late", [span(later)]),
                ("delta", [span(early), span(later)]),
            )
        ],
    }
    final_coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                **check,
                "evidence": [span(prior)]
                if check["requirement_id"] == "early"
                else [span(prior), span(later)],
            }
            for check in coverage["checks"]
            if check["requirement_id"] in {"early", "delta"}
        ],
    }
    first = [wrong, future, later, prior] if cross_page else [wrong, prior, later]
    result, retrieval, _ = await run_repair(
        [(first, {}), *([([prior], {})] if recover else [])],
        queries=["resident individual threshold schedule"],
        requirements=requirements,
        coverage=coverage,
        user_query=question,
        normalized_scope=normalize_request_scope(question),
        preceding_pages=preceding_pages if cross_page else None,
        max_followup_rounds=int(recover),
        followup_queries=[
            {"query": "operative resident individual threshold", "requirement_ids": ["early"]}
        ]
        if recover
        else None,
        final_coverage=final_coverage if recover else None,
    )
    checks = {item["requirement_id"]: item for item in result.diagnostics["coverage"]["checks"]}
    expected_early = recover or not use_wrong_band
    assert (checks["early"]["fulfillment"] == "full") is expected_early
    assert result.diagnostics["coverage"]["full_coverage_validated"] is (
        same_snapshot and expected_early
    )
    assert not result.diagnostics.get("adjacent_queries")
    if cross_page:
        retrieval.preceding_indexed_text.assert_awaited_once_with(
            [wrong.chunk_id, future.chunk_id], index_build_id="build-a"
        )
        assert result.diagnostics["preceding_page_assessment_periods"] == {
            str(wrong.chunk_id): ["2026-2027", "2027-2028"],
            str(future.chunk_id): ["2030-2031"],
        }
        review_ids = result.diagnostics["review_inputs"][0]["chunk_ids"]
        assert str(future.chunk_id) not in review_ids
        assert set(review_ids[:2]) == {str(prior.chunk_id), str(later.chunk_id)}
    if recover:
        assert result.diagnostics["focused_queries"] == [
            "operative resident individual threshold AY 2025-2026"
        ]
    if not expected_early or not same_snapshot:
        assert checks["delta"]["fulfillment"] == "partial"
        return
    assert {item.chunk_id for item in result.selected} == {prior.chunk_id, later.chunk_id}
    registry = _calculation_operands(question, result.selected)
    refs = {Decimal(row["value"]): key for key, row in registry["bindings"].items()}
    assert Decimal(375000) not in refs
    assert Decimal(500000) not in refs
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "delta",
                    "operation": "subtraction",
                    "operands": [refs[Decimal(400000)], refs[Decimal(350000)]],
                    "result": "50000",
                }
            ]
        }
    )
    args = (registry["inputs"], registry["sources"], registry["bindings"])
    assert graph.component_expressions(*args)[1] == {"delta": "incompatible_period"}
    pairs = _verified_comparison_pairs({"knowledge_repair": result.diagnostics})
    assert graph.component_expressions(*args, comparison_operand_pairs=pairs) == (
        {"delta": "400000 - 350000 = 50000"},
        {},
    )


def comparison():
    sources = [
        chunk("For AY 2024-25 the allowance rate is 12%."),
        chunk("For AY 2025-26 the allowance rate is 15%."),
    ]
    requirements = [
        {"requirement_id": "Y1", "description": "Allowance rate for AY 2024-25"},
        {"requirement_id": "Y2", "description": "Allowance rate for AY 2025-26"},
    ]
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "requirement_id": item["requirement_id"],
                "supported": True,
                "fulfillment": "full",
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
            }
            for item, source in zip(requirements, sources, strict=True)
        ],
    }
    return sources, requirements, coverage


@pytest.mark.parametrize("same_snapshot", [True, False])
def test_independently_scoped_corroboration_does_not_require_cross_document_edges(same_snapshot):
    project = uuid.uuid4()
    quote = "For AY 2025-26 the resident individual allowance rate is 15%."
    sources = [
        replace(
            chunk(quote),
            project_id=project,
            metadata={
                "source_revision_id": f"revision-{i}",
                "index_build_id": "build-a" if same_snapshot or i == 0 else "build-b",
                "source_metadata_generation": 37,
            },
        )
        for i in range(2)
    ]
    requirements = [
        EvidenceRequirement(
            requirement_id="scope",
            description="Resident individual applicability for AY 2025-26",
            materiality="governing_applicability",
        ),
        EvidenceRequirement(
            requirement_id="rate",
            description="Resident individual allowance rate for AY 2025-26",
            materiality="central_rule",
            depends_on=["scope"],
            dependency_purposes={"scope": "applicability"},
        ),
    ]
    verdict = CoverageVerdict(
        complete=True,
        missing=[],
        checks=[
            {
                "requirement_id": requirement.requirement_id,
                "supported": True,
                "fulfillment": "full",
                "evidence": [
                    {"chunk_id": str(source.chunk_id), "quote": quote} for source in sources
                ],
            }
            for requirement in requirements
        ],
    )
    reviewed = _guard_review_fulfillment(verdict, requirements, sources, QUESTION)
    assert (reviewed.checks[1].fulfillment == "full") is same_snapshot


async def test_recovery_review_uses_one_canonical_scope_and_keeps_both_years():
    sources, requirements, coverage = comparison()
    calls = []
    result, _, inputs = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        normalized_scope=normalize_request_scope(QUESTION),
        calls=calls,
        max_followup_queries=0,
    )
    payload = _coverage_review_payloads(calls)[0]
    assert [
        row["assigned_scope"]["requested_periods"][0]["start_year"]
        for row in payload["requirements"]
    ] == [2024, 2025]
    assert all(
        set(row["assigned_scope"]) == {"requested_periods"} for row in payload["requirements"]
    )
    # The exact canonical object remains in trusted system context, including
    # filters, temporal basis and requested periods; only identical copies go.
    system = calls[1].args[0][0].content
    canonical = json.loads(system.split("Normalized request scope: ", 1)[1].split("\n", 1)[0])
    assert canonical == inputs.normalized_scope.model_dump(mode="json")
    restored = [{**row, "assigned_scope": canonical} for row in payload["requirements"]]
    assert restored == result.diagnostics["requirements"]
    assert result.decision is not None and result.decision.sufficient
    assert result.diagnostics["coverage"]["full_coverage_validated"] is True
    assert {row["requirement_id"] for row in result.diagnostics["coverage"]["checks"]} == {
        "Y1",
        "Y2",
    }


async def test_each_complete_year_specific_requirement_keeps_its_own_period():
    sources, requirements, coverage = comparison()
    result, _, _ = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        max_followup_queries=0,
    )
    assert result.decision is not None and result.decision.sufficient
    assert all(row["fulfillment"] == "full" for row in result.diagnostics["coverage"]["checks"])


async def test_unqualified_requirement_still_inherits_both_requested_periods():
    sources, requirements, coverage = comparison()
    requirements = [{**requirements[0], "description": "Allowance rate"}]
    coverage["checks"] = coverage["checks"][:1]
    result, _, _ = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        max_followup_queries=0,
    )
    assert result.decision is None and not result.selected
    check = result.diagnostics["coverage"]["checks"][0]
    assert check["fulfillment"] == "partial"
    assert "requested period 2025-26" in check["unresolved_facets"]


@pytest.mark.parametrize("failure", ["missing_own_year", "wrong_year", "foreign_proof"])
async def test_period_and_proof_gaps_still_block_comparison(failure):
    sources, requirements, coverage = comparison()
    if failure == "missing_own_year":
        coverage["checks"][1]["evidence"][0]["quote"] = "the allowance rate is 15%."
    elif failure == "wrong_year":
        coverage["checks"][1]["evidence"] = coverage["checks"][0]["evidence"]
    else:
        coverage["checks"][1]["evidence"][0]["chunk_id"] = str(uuid.uuid4())
    result, _, _ = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        max_followup_queries=0,
    )
    assert result.decision is None
    assert not result.selected
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False


async def test_comparison_review_deadline_never_certifies_its_unfinished_proof():
    sources, requirements, coverage = comparison()

    async def unfinished(messages):
        if "Check whether supplied evidence" in messages[0].content:
            await asyncio.Event().wait()

    result, _, _ = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        generation_hook=unfinished,
        timeout_seconds=0.1,
    )
    assert result.failure is not None
    assert result.diagnostics["stop_reason"] == "recovery_deadline_exceeded"
    assert result.decision is None and not result.selected


@pytest.mark.parametrize("label", ["2025-26", "2025\u201326", "২০২৫\u2013২৬"])
async def test_explicit_requirement_period_does_not_inherit_the_other_year(label):
    sources, requirements, coverage = comparison()
    requirements[1]["description"] = f"Allowance rate for AY {label}"
    result, _, _ = await run_repair(
        [(sources, {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        max_followup_queries=0,
    )
    assert result.decision is not None and result.decision.sufficient


def test_scope_payload_preserves_distinct_overrides_and_every_other_field():
    canonical = {
        "subject": QUESTION,
        "requested_periods": [{"start_year": 2024, "end_year": 2025}],
        "jurisdiction": "example",
        "source_restriction": "indexed_only",
        "exact_as_of": "2026-01-01",
        "project_defaults": "Preserve the source conditions.",
    }
    override = {**canonical, "requested_periods": [{"start_year": 2025, "end_year": 2026}]}
    requirements = [
        {"requirement_id": "Y1", "assigned_scope": dict(canonical), "depends_on": ["base"]},
        {
            "requirement_id": "Y2",
            "assigned_scope": override,
            "dependency_purposes": {"base": "applicability"},
        },
        {"requirement_id": "unknown", "assigned_scope": None, "required": False},
    ]
    payload = _review_requirements_payload(requirements, canonical)
    assert "assigned_scope" not in payload[0]
    assert payload[1]["assigned_scope"] == override and payload[2]["assigned_scope"] is None
    assert [
        {**row, "assigned_scope": row.get("assigned_scope", canonical)} for row in payload
    ] == requirements
    assert requirements[0]["assigned_scope"] == canonical


@pytest.mark.parametrize("both_years", [False, True])
async def test_difference_dependency_still_requires_both_period_operands(both_years):
    sources, requirements, coverage = comparison()
    project, document = uuid.uuid4(), uuid.uuid4()
    metadata = {"index_build_id": "build-a", "source_metadata_generation": 24}
    sources = [
        replace(s, project_id=project, document_id=document, metadata=metadata) for s in sources
    ]
    unit = replace(
        chunk("Both allowances use the same unit."),
        project_id=project,
        document_id=document,
        metadata=metadata,
    )
    requirements.append(
        {
            "requirement_id": "D",
            "description": "Allowance rate difference for AY 2024-25 and AY 2025-26",
            "depends_on": ["Y1", "Y2"],
            "dependency_purposes": {"Y1": "calculation_operand", "Y2": "calculation_operand"},
        }
    )
    coverage["checks"].append(
        {
            "requirement_id": "D",
            "supported": True,
            "fulfillment": "full",
            "evidence": [{"chunk_id": str(unit.chunk_id), "quote": unit.content}],
        }
    )
    if not both_years:
        coverage["checks"][1].update(supported=False, fulfillment="none", evidence=[])
        coverage.update(complete=False, missing=["Allowance rate for AY 2025-26"])
    result, _, _ = await run_repair(
        [([*sources, unit], {})],
        queries=["allowance by year"],
        requirements=requirements,
        coverage=coverage,
        user_query=QUESTION,
        max_followup_queries=0,
    )
    check = next(c for c in result.diagnostics["coverage"]["checks"] if c["requirement_id"] == "D")
    assert (check["fulfillment"] == "full") is both_years
    assert result.diagnostics["coverage"]["full_coverage_validated"] is both_years
