"""Deterministic quality metrics for persisted case outputs."""

from __future__ import annotations

import math
import statistics
from typing import Any


def compute_profile_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [result for result in results if not result["expected_no_answer"]]
    filtered = [result for result in answerable if result["kind"] == "metadata_filter"]
    no_answer = [result for result in results if result["expected_no_answer"]]
    latencies = [float(result["latency_ms"]) for result in results]
    metrics: dict[str, Any] = {
        "case_count": float(len(results)),
        "recall_at_k": _mean([float(result["recall"]) for result in answerable]),
        "recall_at_5": _mean(
            [float(result.get("recall_at_5", result["recall"])) for result in answerable]
        ),
        "recall_at_10": _mean(
            [float(result.get("recall_at_10", result["recall"])) for result in answerable]
        ),
        "rank_1_accuracy": _mean(
            [float(result.get("rank_1_relevant", False)) for result in answerable]
        ),
        "mrr": _mean([float(result["reciprocal_rank"]) for result in answerable]),
        "ndcg": _mean([float(result["ndcg"]) for result in answerable]),
        "filtered_correctness": _mean([float(result["filter_correct"]) for result in filtered]),
        "no_result_behavior": _mean(
            [float(result["insufficient_evidence_reason"] is not None) for result in no_answer]
        ),
        "false_refusal_rate": _rate(
            sum(result["insufficient_evidence_reason"] is not None for result in answerable),
            len(answerable),
        ),
        "false_accept_rate": _rate(
            sum(result["insufficient_evidence_reason"] is None for result in no_answer),
            len(no_answer),
        ),
        "accepted_without_relevant_evidence_rate": _rate(
            sum(
                bool(result.get("accepted_without_relevant_evidence", False))
                for result in answerable
                if result["insufficient_evidence_reason"] is None
            ),
            sum(result["insufficient_evidence_reason"] is None for result in answerable),
        ),
        "groundedness": _mean(
            [
                float(result["grounded"])
                for result in answerable
                if result["insufficient_evidence_reason"] is None
            ]
        ),
        "citation_coverage": _mean(
            [
                float(result["citation_coverage"])
                for result in answerable
                if result["insufficient_evidence_reason"] is None
            ]
        ),
        "answer_token_coverage": _mean(
            [float(result["answer_token_coverage"]) for result in answerable]
        ),
        "unverified_claim_rate": _mean_or_zero(
            [
                float(result.get("unverified_claim_rate", 0.0))
                for result in answerable
                if result["insufficient_evidence_reason"] is None
            ]
        ),
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "reranker_unavailable_count": float(
            sum(result["rerank_status"] == "unavailable" for result in results)
        ),
        "candidate_union_recall": _mean(
            [
                float(result.get("candidate_union_relevant", result.get("relevant_retrieved", 0.0)))
                for result in answerable
            ]
        ),
        "translated_branch_recall": _mean(
            [float(result.get("translated_branch_contributed", False)) for result in answerable]
        ),
        "translation_latency_p50_ms": _optional_latency_median(results, "translation_latency_ms"),
        "translation_latency_p95_ms": _optional_latency_percentile(
            results, "translation_latency_ms"
        ),
        "reranker_latency_p50_ms": _optional_latency_median(results, "reranker_latency_ms"),
        "reranker_latency_p95_ms": _optional_latency_percentile(results, "reranker_latency_ms"),
    }
    language_groups = _group_language_pairs(answerable)
    metrics["language_pairs"] = {
        pair: _pair_metrics(rows) for pair, rows in language_groups.items()
    }
    metrics["query_forms"] = {
        form: _pair_metrics(rows) for form, rows in _group_query_forms(answerable).items()
    }
    metrics["semantic_score_calibration"] = _semantic_score_calibration(results)
    metrics["passage_semantic_score_calibration"] = _passage_semantic_score_calibration(results)
    metrics["reranker_relevance_calibration"] = _score_calibration(
        results,
        positive_field="best_relevant_rerank_score",
        hard_negative_field="best_hard_negative_rerank_score",
    )
    return metrics


def rank_metrics(
    result_ids: list[str],
    relevant_ids: set[str],
) -> tuple[float, float, float, bool]:
    if not relevant_ids:
        return 1.0, 1.0, 1.0, True
    relevance = [1 if result_id in relevant_ids else 0 for result_id in result_ids]
    found = sum(relevance)
    recall = min(found / len(relevant_ids), 1.0)
    first_rank = next((index for index, value in enumerate(relevance, start=1) if value), None)
    reciprocal_rank = 1.0 / first_rank if first_rank is not None else 0.0
    dcg = sum(value / math.log2(index + 1) for index, value in enumerate(relevance, start=1))
    ideal_count = min(len(relevant_ids), len(result_ids))
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_count + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return recall, reciprocal_rank, ndcg, found > 0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 1.0


def _mean_or_zero(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _pair_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "case_count": float(len(rows)),
        "recall_at_k": _mean([float(row["recall"]) for row in rows]),
        "recall_at_5": _mean([float(row.get("recall_at_5", row["recall"])) for row in rows]),
        "recall_at_10": _mean([float(row.get("recall_at_10", row["recall"])) for row in rows]),
        "ndcg": _mean([float(row["ndcg"]) for row in rows]),
        "mrr": _mean([float(row["reciprocal_rank"]) for row in rows]),
        "rank_1_accuracy": _mean([float(row.get("rank_1_relevant", False)) for row in rows]),
        "false_refusal_rate": _rate(
            sum(row["insufficient_evidence_reason"] is not None for row in rows),
            len(rows),
        ),
        "translated_branch_recall": _mean(
            [float(row.get("translated_branch_contributed", False)) for row in rows]
        ),
    }


def _group_query_forms(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        form = result.get("query_form")
        if not isinstance(form, str) or not form:
            continue
        grouped.setdefault(form, []).append(result)
    return dict(sorted(grouped.items()))


def _optional_latencies(results: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _optional_latency_median(results: list[dict[str, Any]], field: str) -> float:
    values = _optional_latencies(results, field)
    return statistics.median(values) if values else 0.0


def _optional_latency_percentile(results: list[dict[str, Any]], field: str) -> float:
    return _percentile(_optional_latencies(results, field), 0.95)


def _group_language_pairs(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        query_language = result.get("query_language")
        evidence_language = result.get("expected_evidence_language")
        if not isinstance(query_language, str) or not isinstance(evidence_language, str):
            continue
        grouped.setdefault(f"{query_language}->{evidence_language}", []).append(result)
    return dict(sorted(grouped.items()))


def _semantic_score_calibration(results: list[dict[str, Any]]) -> dict[str, Any]:
    return _score_calibration(
        results,
        positive_field="best_relevant_semantic_score",
        hard_negative_field="best_hard_negative_semantic_score",
    )


def _passage_semantic_score_calibration(results: list[dict[str, Any]]) -> dict[str, Any]:
    return _score_calibration(
        results,
        positive_field="best_relevant_passage_semantic_score",
        hard_negative_field="best_hard_negative_passage_semantic_score",
    )


def _score_calibration(
    results: list[dict[str, Any]],
    *,
    positive_field: str,
    hard_negative_field: str,
) -> dict[str, Any]:
    def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
        positives = [
            float(row[positive_field]) for row in rows if row.get(positive_field) is not None
        ]
        hard_negatives = [
            float(row[hard_negative_field])
            for row in rows
            if row.get(hard_negative_field) is not None
        ]
        return {
            "positive_count": float(len(positives)),
            "positive_min": min(positives) if positives else 0.0,
            "positive_p50": statistics.median(positives) if positives else 0.0,
            "hard_negative_count": float(len(hard_negatives)),
            "hard_negative_max": max(hard_negatives) if hard_negatives else 0.0,
            "hard_negative_p95": _percentile(hard_negatives, 0.95),
            "observed_margin": (
                min(positives) - max(hard_negatives) if positives and hard_negatives else 0.0
            ),
        }

    return {
        "overall": summarize(results),
        "language_pairs": {
            pair: summarize(rows) for pair, rows in _group_language_pairs(results).items()
        },
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]
