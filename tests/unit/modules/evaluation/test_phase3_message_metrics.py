"""Complete-turn metrics never count failed execution as successful abstention."""

from __future__ import annotations

import pytest

from app.modules.evaluation.metrics import compute_profile_metrics

pytestmark = pytest.mark.unit


def row(expected, actual, *, claims=0, attempts=0, rejected=0, complete=900):
    return {
        "expected_no_answer": expected in {"insufficient_evidence", "unresolved_authority"},
        "expected_outcome": expected,
        "kind": "citation",
        "recall": 1.0,
        "reciprocal_rank": 1.0,
        "ndcg": 1.0,
        "filter_correct": True,
        "latency_ms": 100,
        "search_latency_ms": 100,
        "complete_turn_latency_ms": complete,
        "rerank_status": "disabled",
        "grounded": bool(claims),
        "claims": [
            {"claim_id": str(i), "verification": "supported", "grounded": True}
            for i in range(claims)
        ],
        "insufficient_evidence_reason": None if actual in {"answered", "partial"} else actual,
        "citation_coverage": 1.0 if claims else 0.0,
        "citation_coverage_status": "applicable" if claims else "not_applicable",
        "answer_token_coverage": 1.0,
        "execution": {
            "terminal": {"outcome": actual},
            "attempted_assertions": attempts,
            "rejected_assertions": rejected,
        },
    }


def test_partial_abstention_failure_and_latency_use_explicit_denominators():
    rows = [
        row("answered", "answered", claims=2, attempts=2),
        row("partial", "partial", claims=1, attempts=3, rejected=2),
        row("partial", "verification_failed", attempts=1, rejected=1),
        row("insufficient_evidence", "insufficient_evidence"),
        row("insufficient_evidence", "timed_out"),
        row("unresolved_authority", "unresolved_authority"),
    ]
    metrics = compute_profile_metrics(rows)
    assert metrics["correct_partial_answer_rate"] == 0.5 and metrics["partial_case_count"] == 2
    assert metrics["correct_abstention_rate"] == pytest.approx(2 / 3)
    assert metrics["abstention_case_count"] == 3
    assert metrics["verification_failure_count"] == 1 and metrics["timeout_count"] == 1
    assert metrics["execution_failure_count"] == 2
    assert metrics["search_latency_p50_ms"] == 100
    assert metrics["complete_turn_latency_p50_ms"] == 900
    assert metrics["attempted_assertion_count"] == 6
    assert metrics["rejected_assertion_count"] == 3 and metrics["published_assertion_count"] == 3
    assert metrics["factual_citation_case_count"] == 2 and metrics["citation_coverage"] == 1.0


def test_no_facts_and_no_complete_turn_observation_are_not_perfect_scores():
    metrics = compute_profile_metrics(
        [row("insufficient_evidence", "insufficient_evidence", complete=None)]
    )
    assert (
        metrics["citation_coverage_status"] == "not_applicable"
        and metrics["citation_coverage"] == 0.0
    )
    assert metrics["complete_turn_latency_sample_count"] == 0
    assert metrics["complete_turn_latency_p95_ms"] is None
    assert metrics["correct_partial_answer_rate"] == 0.0
    assert metrics["correct_abstention_rate"] == 1.0
