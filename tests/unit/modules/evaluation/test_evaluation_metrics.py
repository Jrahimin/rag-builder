"""Unit tests for reproducible evaluation metrics."""

from __future__ import annotations

import pytest

from app.modules.evaluation.metrics import compute_profile_metrics, rank_metrics

pytestmark = pytest.mark.unit


def test_rank_metrics_reports_recall_mrr_and_ndcg() -> None:
    recall, mrr, ndcg, found = rank_metrics(["other", "relevant"], {"relevant"})
    assert recall == 1.0
    assert mrr == 0.5
    assert ndcg == pytest.approx(1 / 1.5849625007)
    assert found is True


def test_profile_metrics_include_refusal_grounding_citations_and_latency() -> None:
    results = [
        {
            "expected_no_answer": False,
            "kind": "citation",
            "recall": 1.0,
            "reciprocal_rank": 1.0,
            "ndcg": 1.0,
            "relevant_retrieved": True,
            "filter_correct": True,
            "insufficient_evidence_reason": None,
            "grounded": True,
            "citation_coverage": 1.0,
            "answer_token_coverage": 1.0,
            "latency_ms": 100,
            "rerank_status": "applied",
        },
        {
            "expected_no_answer": True,
            "kind": "no_answer",
            "recall": 1.0,
            "reciprocal_rank": 1.0,
            "ndcg": 1.0,
            "relevant_retrieved": True,
            "filter_correct": True,
            "insufficient_evidence_reason": "no_retrieval_results",
            "grounded": False,
            "citation_coverage": 1.0,
            "answer_token_coverage": 1.0,
            "latency_ms": 200,
            "rerank_status": "unavailable",
        },
    ]
    metrics = compute_profile_metrics(results)
    assert metrics["recall_at_k"] == 1.0
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["groundedness"] == 1.0
    assert metrics["citation_coverage"] == 1.0
    assert metrics["latency_p95_ms"] == 200
    assert metrics["reranker_unavailable_count"] == 1


def test_filtered_correctness_rejects_results_outside_the_requested_filter() -> None:
    metrics = compute_profile_metrics(
        [
            {
                "expected_no_answer": False,
                "kind": "metadata_filter",
                "recall": 1.0,
                "reciprocal_rank": 1.0,
                "ndcg": 1.0,
                "relevant_retrieved": True,
                "filter_correct": False,
                "insufficient_evidence_reason": None,
                "grounded": True,
                "citation_coverage": 1.0,
                "answer_token_coverage": 1.0,
                "latency_ms": 10,
                "rerank_status": "disabled",
            }
        ]
    )

    assert metrics["filtered_correctness"] == 0.0
