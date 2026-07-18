"""Execute identical quality cases across retrieval/reranker profiles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import EvaluationConfig
from app.core.exceptions import NotFoundError
from app.modules.evaluation.errors import EvaluationCorpusChangedError
from app.modules.evaluation.metrics import compute_profile_metrics, rank_metrics
from app.modules.evaluation.ports import EvaluationAnswerPort, EvaluationRetrievalPort
from app.modules.evaluation.repositories.evaluation_corpus_repository import (
    EvaluationCorpusRepository,
)
from app.modules.evaluation.repositories.evaluation_dataset_repository import (
    EvaluationDatasetRepository,
)
from app.modules.evaluation.repositories.evaluation_run_repository import EvaluationRunRepository
from app.modules.evaluation.schemas.evaluation import EvaluationCase
from app.platform.domain.text_tokenization import tokenize
from app.platform.jobs.contracts import JobProgressCallback


class EvaluationRunnerService:
    """Run and persist a complete, reproducible comparison."""

    def __init__(
        self,
        *,
        runs: EvaluationRunRepository,
        datasets: EvaluationDatasetRepository,
        corpus: EvaluationCorpusRepository,
        retrieval: EvaluationRetrievalPort,
        answerer: EvaluationAnswerPort,
        config: EvaluationConfig,
    ) -> None:
        self._runs = runs
        self._datasets = datasets
        self._corpus = corpus
        self._retrieval = retrieval
        self._answerer = answerer
        self._config = config

    async def run(
        self,
        run_id: uuid.UUID,
        *,
        on_progress: JobProgressCallback | None = None,
    ) -> None:
        run = await self._runs.get_by_id(run_id)
        if run is None:
            raise NotFoundError(
                message="Evaluation run not found.",
                code="evaluation_run_not_found",
            )
        dataset = await self._datasets.get_by_id(run.dataset_id)
        if dataset is None:  # pragma: no cover - protected by FK
            raise NotFoundError(
                message="Evaluation dataset not found.",
                code="evaluation_dataset_not_found",
            )
        captured_corpus = dict(run.versions["corpus"])
        current_corpus = await self._corpus.snapshot(
            embedding_set_version=int(captured_corpus["embedding_set_version"]),
            embedding_provider=str(captured_corpus["embedding_provider"]),
            embedding_model=str(captured_corpus["embedding_model"]),
        )
        if current_corpus["fingerprint"] != captured_corpus["fingerprint"]:
            raise EvaluationCorpusChangedError(
                "The indexed corpus changed after this evaluation was queued. Queue a new run.",
                context={
                    "captured_fingerprint": captured_corpus["fingerprint"],
                    "current_fingerprint": current_corpus["fingerprint"],
                },
            )
        cases = [EvaluationCase.model_validate(value) for value in dataset.cases]
        total_steps = max(len(cases) * len(self._retrieval.profiles), 1)
        completed_steps = 0
        all_results: list[dict[str, Any]] = []

        for case in cases:
            for profile in self._retrieval.profiles:
                search = await self._retrieval.search(
                    profile=profile,
                    query=case.query,
                    top_k=run.top_k,
                    document_id=case.document_id,
                    metadata_filter=case.metadata_filter,
                )
                answer = await self._answerer.answer(question=case.query, hits=search.hits)
                all_results.append(_case_result(case, profile, search, answer))
                completed_steps += 1
                if on_progress is not None:
                    await on_progress(
                        f"evaluating:{case.key}:{profile}",
                        min(int(completed_steps / total_steps * 95), 95),
                    )

        metrics = {
            profile: compute_profile_metrics(
                [result for result in all_results if result["profile"] == profile]
            )
            for profile in self._retrieval.profiles
        }
        primary = self._retrieval.primary_profile
        previous = await self._runs.latest_completed_before(
            dataset_id=run.dataset_id,
            run_id=run.id,
        )
        run.metrics = metrics
        run.case_results = all_results
        run.regressions = _regressions(
            current=metrics.get(primary, {}),
            previous=(previous.metrics.get(primary, {}) if previous is not None else {}),
            tolerance=self._config.maximum_metric_regression,
        )
        run.failed_cases = _failed_cases(
            [result for result in all_results if result["profile"] == primary]
        )
        run.reranker_comparison = _reranker_comparison(
            metrics=metrics,
            case_results=all_results,
            profile_metadata=self._retrieval.profile_metadata,
            primary_profile=primary,
            config=self._config,
        )
        run.completed_at = datetime.now(UTC)
        await self._runs.flush()
        if on_progress is not None:
            await on_progress("evaluation_complete", 100)


def _case_result(case: EvaluationCase, profile: str, search: Any, answer: Any) -> dict[str, Any]:
    use_chunks = bool(case.relevant_chunk_ids)
    result_ids = [
        str(hit.chunk_id if use_chunks else hit.document_id)
        for hit in search.hits
    ]
    relevant_ids = {
        str(value)
        for value in (case.relevant_chunk_ids if use_chunks else case.relevant_document_ids)
    }
    recall, reciprocal_rank, ndcg, relevant_retrieved = rank_metrics(result_ids, relevant_ids)
    filter_correct = relevant_retrieved and bool(search.hits) and all(
        (case.document_id is None or hit.document_id == case.document_id)
        and all(str(hit.metadata.get(key)) == value for key, value in case.metadata_filter.items())
        for hit in search.hits
    )
    expected_tokens = {token.lower() for token in case.expected_answer_tokens}
    answer_tokens = set(tokenize(answer.answer, for_query=True))
    token_coverage = (
        len(expected_tokens & answer_tokens) / len(expected_tokens) if expected_tokens else 1.0
    )
    return {
        "case_key": case.key,
        "kind": case.kind.value,
        "profile": profile,
        "query": case.query,
        "expected_no_answer": case.expected_no_answer,
        "result_chunk_ids": [str(hit.chunk_id) for hit in search.hits],
        "result_document_ids": [str(hit.document_id) for hit in search.hits],
        "recall": recall,
        "reciprocal_rank": reciprocal_rank,
        "ndcg": ndcg,
        "relevant_retrieved": relevant_retrieved,
        "filter_correct": filter_correct,
        "latency_ms": search.latency_ms,
        "rerank_status": search.rerank_status,
        "reranker_provider": search.reranker_provider,
        "reranker_model": search.reranker_model,
        "reranker_version": search.reranker_version,
        "answer": answer.answer,
        "insufficient_evidence_reason": answer.insufficient_evidence_reason,
        "grounded": answer.grounded,
        "citation_coverage": answer.citation_coverage,
        "claims": answer.claims,
        "answer_token_coverage": token_coverage,
    }


def _failed_cases(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for result in results:
        reasons: list[str] = []
        refused = result["insufficient_evidence_reason"] is not None
        if bool(result["expected_no_answer"]) != refused:
            reasons.append("refusal_mismatch")
        if not result["expected_no_answer"] and not result["relevant_retrieved"]:
            reasons.append("relevant_evidence_not_retrieved")
        if result["kind"] == "metadata_filter" and not result["filter_correct"]:
            reasons.append("filter_mismatch")
        if not refused and not result["grounded"]:
            reasons.append("ungrounded_answer")
        if not refused and result["citation_coverage"] < 1.0:
            reasons.append("incomplete_citation_coverage")
        if result["answer_token_coverage"] < 1.0:
            reasons.append("expected_answer_tokens_missing")
        if reasons:
            failed.append({"case_key": result["case_key"], "reasons": reasons})
    return failed


def _regressions(
    *,
    current: dict[str, Any],
    previous: dict[str, Any],
    tolerance: float,
) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    for metric in (
        "recall_at_k",
        "mrr",
        "ndcg",
        "filtered_correctness",
        "refusal_accuracy",
        "groundedness",
        "citation_coverage",
    ):
        if metric not in previous or metric not in current:
            continue
        delta = float(current[metric]) - float(previous[metric])
        if delta < -tolerance:
            regressions.append(
                {
                    "metric": metric,
                    "previous": previous[metric],
                    "current": current[metric],
                    "delta": delta,
                }
            )
    return regressions


def _reranker_comparison(
    *,
    metrics: dict[str, dict[str, Any]],
    case_results: list[dict[str, Any]],
    profile_metadata: dict[str, dict[str, Any]],
    primary_profile: str,
    config: EvaluationConfig,
) -> dict[str, Any]:
    baseline = metrics.get("hybrid", {})
    comparisons: list[dict[str, Any]] = []
    for profile, candidate in metrics.items():
        if not profile.startswith("reranked_"):
            continue
        unavailable_count = int(candidate.get("reranker_unavailable_count", 0))
        ndcg_gain = float(candidate.get("ndcg", 0.0)) - float(baseline.get("ndcg", 0.0))
        groundedness_gain = float(candidate.get("groundedness", 0.0)) - float(
            baseline.get("groundedness", 0.0)
        )
        latency_penalty = float(candidate.get("latency_p95_ms", 0.0)) - float(
            baseline.get("latency_p95_ms", 0.0)
        )
        metadata = profile_metadata.get(profile, {})
        learned = bool(metadata.get("learned", False))
        candidate_eligible = (
            learned
            and ndcg_gain >= config.minimum_reranker_ndcg_gain
            and groundedness_gain >= 0.0
            and latency_penalty <= config.maximum_reranker_latency_penalty_ms
            and unavailable_count == 0
        )
        profile_rows = [row for row in case_results if row["profile"] == profile]
        comparisons.append(
            {
                "profile": profile,
                "provider": next(
                    (row["reranker_provider"] for row in profile_rows if row["reranker_provider"]),
                    metadata.get("provider"),
                ),
                "model": next(
                    (row["reranker_model"] for row in profile_rows if row["reranker_model"]),
                    metadata.get("model"),
                ),
                "version": next(
                    (row["reranker_version"] for row in profile_rows if row["reranker_version"]),
                    metadata.get("version"),
                ),
                "learned": learned,
                "recall_gain": float(candidate.get("recall_at_k", 0.0))
                - float(baseline.get("recall_at_k", 0.0)),
                "ndcg_gain": ndcg_gain,
                "groundedness_gain": groundedness_gain,
                "p95_latency_penalty_ms": latency_penalty,
                "unavailable_count": unavailable_count,
                "operational_fit": latency_penalty
                <= config.maximum_reranker_latency_penalty_ms,
                "eligible_for_promotion": candidate_eligible,
            }
        )
    eligible_candidates = [
        item for item in comparisons if item["eligible_for_promotion"]
    ]
    recommended = max(
        eligible_candidates,
        key=lambda item: item["ndcg_gain"],
        default=None,
    )
    return {
        "baseline_profile": "hybrid",
        "active_profile": primary_profile,
        "acceptance": {
            "minimum_ndcg_gain": config.minimum_reranker_ndcg_gain,
            "maximum_p95_latency_penalty_ms": config.maximum_reranker_latency_penalty_ms,
            "requires_nonnegative_groundedness_gain": True,
            "requires_zero_unavailable_cases": True,
            "requires_learned_model": True,
        },
        "candidates": comparisons,
        "recommended_profile": recommended["profile"] if recommended is not None else None,
        "promotion_reason": (
            "candidate_meets_all_acceptance_thresholds"
            if recommended is not None
            else "no_learned_candidate_met_all_acceptance_thresholds"
        ),
    }
