"""Deterministic quality metrics for persisted case outputs."""

from __future__ import annotations

import math
import statistics
from typing import Any


def compute_profile_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    answerable = [result for result in results if not result["expected_no_answer"]]
    filtered = [result for result in answerable if result["kind"] == "metadata_filter"]
    no_answer = [result for result in results if result["expected_no_answer"]]
    latencies = [float(result["latency_ms"]) for result in results]
    return {
        "case_count": float(len(results)),
        "recall_at_k": _mean([float(result["recall"]) for result in answerable]),
        "mrr": _mean([float(result["reciprocal_rank"]) for result in answerable]),
        "ndcg": _mean([float(result["ndcg"]) for result in answerable]),
        "filtered_correctness": _mean(
            [float(result["filter_correct"]) for result in filtered]
        ),
        "no_result_behavior": _mean(
            [float(result["insufficient_evidence_reason"] is not None) for result in no_answer]
        ),
        "refusal_accuracy": _mean(
            [
                float(
                    (result["insufficient_evidence_reason"] is not None)
                    == bool(result["expected_no_answer"])
                )
                for result in results
            ]
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
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "reranker_unavailable_count": float(
            sum(result["rerank_status"] == "unavailable" for result in results)
        ),
    }


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


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]
