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
            "query_language": "en",
            "expected_evidence_language": "bn",
            "recall": 1.0,
            "reciprocal_rank": 1.0,
            "ndcg": 1.0,
            "relevant_retrieved": True,
            "rank_1_relevant": True,
            "accepted_without_relevant_evidence": False,
            "best_relevant_semantic_score": 0.72,
            "best_hard_negative_semantic_score": 0.21,
            "filter_correct": True,
            "insufficient_evidence_reason": None,
            "grounded": True,
            "citation_coverage": 1.0,
            "answer_token_coverage": 1.0,
            "unverified_claim_rate": 0.5,
            "latency_ms": 100,
            "rerank_status": "applied",
        },
        {
            "expected_no_answer": True,
            "kind": "no_answer",
            "query_language": "en",
            "expected_evidence_language": "en",
            "recall": 1.0,
            "reciprocal_rank": 1.0,
            "ndcg": 1.0,
            "relevant_retrieved": True,
            "rank_1_relevant": True,
            "accepted_without_relevant_evidence": False,
            "best_relevant_semantic_score": None,
            "best_hard_negative_semantic_score": 0.25,
            "filter_correct": True,
            "insufficient_evidence_reason": "no_retrieval_results",
            "grounded": False,
            "citation_coverage": 1.0,
            "answer_token_coverage": 1.0,
            "unverified_claim_rate": 0.0,
            "latency_ms": 200,
            "rerank_status": "unavailable",
        },
    ]
    metrics = compute_profile_metrics(results)
    assert metrics["recall_at_k"] == 1.0
    assert metrics["rank_1_accuracy"] == 1.0
    assert metrics["false_refusal_rate"] == 0.0
    assert metrics["false_accept_rate"] == 0.0
    assert metrics["accepted_without_relevant_evidence_rate"] == 0.0
    assert metrics["groundedness"] == 1.0
    assert metrics["citation_coverage"] == 1.0
    assert metrics["unverified_claim_rate"] == 0.5
    assert metrics["language_pairs"]["en->bn"]["recall_at_k"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["candidate_union_recall"] == 1.0
    assert metrics["query_forms"] == {}
    calibration = metrics["semantic_score_calibration"]["overall"]
    assert calibration["positive_min"] == 0.72
    assert calibration["hard_negative_max"] == 0.25
    assert calibration["observed_margin"] == pytest.approx(0.47)
    assert metrics["latency_p95_ms"] == 200
    assert metrics["reranker_unavailable_count"] == 1


def test_filtered_correctness_rejects_results_outside_the_requested_filter() -> None:
    metrics = compute_profile_metrics(
        [
            {
                "expected_no_answer": False,
                "kind": "metadata_filter",
                "query_language": None,
                "expected_evidence_language": None,
                "recall": 1.0,
                "reciprocal_rank": 1.0,
                "ndcg": 1.0,
                "relevant_retrieved": True,
                "rank_1_relevant": True,
                "accepted_without_relevant_evidence": False,
                "best_relevant_semantic_score": 0.8,
                "best_hard_negative_semantic_score": None,
                "filter_correct": False,
                "insufficient_evidence_reason": None,
                "grounded": True,
                "citation_coverage": 1.0,
                "answer_token_coverage": 1.0,
                "unverified_claim_rate": 0.0,
                "latency_ms": 10,
                "rerank_status": "disabled",
            }
        ]
    )

    assert metrics["filtered_correctness"] == 0.0


def test_profile_metrics_separate_false_refusals_from_false_accepts() -> None:
    base = {
        "kind": "cross_lingual",
        "recall": 0.0,
        "reciprocal_rank": 0.0,
        "ndcg": 0.0,
        "filter_correct": True,
        "rank_1_relevant": False,
        "accepted_without_relevant_evidence": False,
        "best_relevant_semantic_score": None,
        "best_hard_negative_semantic_score": 0.3,
        "grounded": False,
        "citation_coverage": 0.0,
        "answer_token_coverage": 0.0,
        "unverified_claim_rate": 0.0,
        "latency_ms": 10,
        "rerank_status": "disabled",
    }
    metrics = compute_profile_metrics(
        [
            {
                **base,
                "expected_no_answer": False,
                "insufficient_evidence_reason": "below_relevance_threshold",
            },
            {
                **base,
                "expected_no_answer": True,
                "insufficient_evidence_reason": None,
            },
        ]
    )

    assert metrics["false_refusal_rate"] == 1.0
    assert metrics["false_accept_rate"] == 1.0
    assert metrics["unverified_claim_rate"] == 0.0


def test_profile_metrics_reject_accepted_answers_without_relevant_evidence() -> None:
    metrics = compute_profile_metrics(
        [
            {
                "expected_no_answer": False,
                "kind": "paraphrase",
                "query_language": "en",
                "expected_evidence_language": "bn",
                "recall": 0.0,
                "rank_1_relevant": False,
                "reciprocal_rank": 0.0,
                "ndcg": 0.0,
                "filter_correct": True,
                "best_relevant_semantic_score": None,
                "best_hard_negative_semantic_score": 0.4,
                "insufficient_evidence_reason": None,
                "accepted_without_relevant_evidence": True,
                "grounded": True,
                "citation_coverage": 1.0,
                "answer_token_coverage": 1.0,
                "unverified_claim_rate": 0.0,
                "latency_ms": 10,
                "rerank_status": "disabled",
            }
        ]
    )

    assert metrics["rank_1_accuracy"] == 0.0
    assert metrics["accepted_without_relevant_evidence_rate"] == 1.0


def test_profile_metrics_aggregate_compact_evidence_funnels_and_user_parameters() -> None:
    row = {
        "expected_no_answer": False,
        "kind": "citation",
        "recall": 1.0,
        "reciprocal_rank": 1.0,
        "ndcg": 1.0,
        "relevant_retrieved": True,
        "rank_1_relevant": True,
        "accepted_without_relevant_evidence": False,
        "filter_correct": True,
        "insufficient_evidence_reason": None,
        "grounded": True,
        "citation_coverage": 1.0,
        "answer_token_coverage": 1.0,
        "unverified_claim_rate": 0.0,
        "latency_ms": 10,
        "rerank_status": "unavailable",
        "user_parameter_tokens": ["75000"],
        "user_parameter_token_coverage": 1.0,
        "evidence_funnel": {
            "fused": 5,
            "reranked": 5,
            "policy_survived": 4,
            "hydrated": 4,
            "deduped": 3,
            "assessed": 3,
            "admitted": 2,
            "context_selected": 2,
            "cited": 1,
            "supported_claims": 1,
            "loss_reasons": {"deduped": {"duplicate_content": 1}},
            "would_have_blocked": False,
            "outcome": "answered",
        },
    }

    metrics = compute_profile_metrics([row])

    assert metrics["evidence_funnel"]["fused"] == 5
    assert metrics["evidence_funnel"]["deduped"] == 3
    assert metrics["evidence_funnel"]["loss_reasons"] == {"deduped": {"duplicate_content": 1}}
    assert metrics["evidence_funnel"]["outcomes"] == {"answered": 1}
    assert metrics["user_parameter_cases"]["token_coverage"] == 1.0
