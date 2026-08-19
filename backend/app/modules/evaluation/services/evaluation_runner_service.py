"""Execute identical quality cases across retrieval/reranker profiles."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter

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
from app.platform.domain.text_normalizer import normalize_for_indexing
from app.platform.domain.text_tokenization import tokenize
from app.platform.jobs.contracts import JobProgressCallback

_JSON_MAPPING = TypeAdapter(dict[str, Any])


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
        started = time.perf_counter()
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
                    as_of=case.as_of,
                )
                answer = await self._answerer.answer(
                    profile=profile,
                    question=case.query,
                    hits=search.hits,
                )
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
        primary_metrics = metrics.get(primary, {})
        run.regressions = [
            *_regressions(
                current=primary_metrics,
                previous=(previous.metrics.get(primary, {}) if previous is not None else {}),
                tolerance=self._config.maximum_metric_regression,
            ),
            *_acceptance_failures(primary_metrics, self._config),
        ]
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
        run.input_tokens = _complete_sum(all_results, "input_tokens")
        run.output_tokens = _complete_sum(all_results, "output_tokens")
        run.retrieval_latency_ms = sum(int(result["latency_ms"]) for result in all_results)
        run.provider_latency_ms = _complete_sum(all_results, "provider_latency_ms")
        run.total_latency_ms = int((time.perf_counter() - started) * 1000)
        if run.provider is None:
            run.provider = next(
                (str(result["provider"]) for result in all_results if result.get("provider")),
                None,
            )
        if run.model is None:
            run.model = next(
                (str(result["model"]) for result in all_results if result.get("model")),
                None,
            )
        run.completed_at = datetime.now(UTC)
        await self._runs.flush()
        if on_progress is not None:
            await on_progress("evaluation_complete", 100)


def _case_result(case: EvaluationCase, profile: str, search: Any, answer: Any) -> dict[str, Any]:
    use_chunks = bool(case.relevant_chunk_ids or case.relevant_evidence_phrases)
    result_ids = [str(hit.chunk_id if use_chunks else hit.document_id) for hit in search.hits]
    if case.relevant_evidence_phrases:
        relevant_ids = {
            str(hit.chunk_id)
            for hit in search.hits
            if _matches_evidence_phrases(hit.content, case.relevant_evidence_phrases)
        }
        if not relevant_ids:
            relevant_ids = {"__expected_evidence_phrase__"}
    else:
        relevant_ids = {
            str(value)
            for value in (case.relevant_chunk_ids if use_chunks else case.relevant_document_ids)
        }
    recall, reciprocal_rank, ndcg, relevant_retrieved = rank_metrics(result_ids, relevant_ids)
    recall_at_5, _, _, _ = rank_metrics(result_ids[:5], relevant_ids)
    recall_at_10, _, _, _ = rank_metrics(result_ids[:10], relevant_ids)
    relevant_semantic_scores = [
        float(hit.semantic_score)
        for hit, result_id in zip(search.hits, result_ids, strict=True)
        if result_id in relevant_ids and hit.semantic_score is not None
    ]
    relevant_passage_scores = [
        float(hit.passage_semantic_score)
        for hit, result_id in zip(search.hits, result_ids, strict=True)
        if result_id in relevant_ids and hit.passage_semantic_score is not None
    ]
    relevant_rerank_scores = [
        float(hit.rerank_relevance_score)
        for hit, result_id in zip(search.hits, result_ids, strict=True)
        if result_id in relevant_ids and hit.rerank_relevance_score is not None
    ]
    hard_negative_hits = [
        hit
        for hit, result_id in zip(search.hits, result_ids, strict=True)
        if result_id not in relevant_ids
        and (
            not case.hard_negative_evidence_phrases
            or _matches_evidence_phrases(hit.content, case.hard_negative_evidence_phrases)
        )
    ]
    hard_negative_semantic_scores = [
        float(hit.semantic_score) for hit in hard_negative_hits if hit.semantic_score is not None
    ]
    hard_negative_passage_scores = [
        float(hit.passage_semantic_score)
        for hit in hard_negative_hits
        if hit.passage_semantic_score is not None
    ]
    hard_negative_rerank_scores = [
        float(hit.rerank_relevance_score)
        for hit in hard_negative_hits
        if hit.rerank_relevance_score is not None
    ]
    relevant_families = sorted(
        {
            family
            for hit, result_id in zip(search.hits, result_ids, strict=True)
            if result_id in relevant_ids
            for family in _hit_branch_families(hit)
        }
    )
    filter_correct = (
        relevant_retrieved
        and bool(search.hits)
        and all(
            (case.document_id is None or hit.document_id == case.document_id)
            and all(
                str(hit.metadata.get(key)) == value for key, value in case.metadata_filter.items()
            )
            for hit in search.hits
        )
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
        "query_language": case.query_language,
        "expected_evidence_language": case.expected_evidence_language,
        "expected_no_answer": case.expected_no_answer,
        "result_chunk_ids": [str(hit.chunk_id) for hit in search.hits],
        "result_document_ids": [str(hit.document_id) for hit in search.hits],
        "result_source_metadata": [
            _JSON_MAPPING.dump_python(dict(hit.metadata), mode="json") for hit in search.hits
        ],
        "recall": recall,
        "recall_at_5": recall_at_5,
        "recall_at_10": recall_at_10,
        "reciprocal_rank": reciprocal_rank,
        "ndcg": ndcg,
        "relevant_retrieved": relevant_retrieved,
        "rank_1_relevant": bool(result_ids and result_ids[0] in relevant_ids),
        "relevant_chunk_rank": next(
            (
                index
                for index, result_id in enumerate(result_ids, start=1)
                if result_id in relevant_ids
            ),
            None,
        ),
        "accepted_without_relevant_evidence": (
            not case.expected_no_answer
            and answer.insufficient_evidence_reason is None
            and not relevant_retrieved
        ),
        "relevant_evidence_phrases": list(case.relevant_evidence_phrases),
        "selected_chunk_ids": [str(chunk_id) for chunk_id in answer.selected_chunk_ids],
        "relevant_in_retrieved": relevant_retrieved,
        "relevant_in_selected": _relevant_in_selected(
            relevant_ids,
            answer.selected_chunk_ids,
            retrieved=relevant_retrieved,
        ),
        "relevant_dropped_before_gate": _relevant_dropped_before_gate(
            relevant_ids,
            result_ids,
            answer.selected_chunk_ids,
        ),
        "best_relevant_semantic_score": (
            max(relevant_semantic_scores) if relevant_semantic_scores else None
        ),
        "best_hard_negative_semantic_score": (
            max(hard_negative_semantic_scores) if hard_negative_semantic_scores else None
        ),
        "best_relevant_passage_semantic_score": (
            max(relevant_passage_scores) if relevant_passage_scores else None
        ),
        "best_hard_negative_passage_semantic_score": (
            max(hard_negative_passage_scores) if hard_negative_passage_scores else None
        ),
        "best_relevant_rerank_score": (
            max(relevant_rerank_scores) if relevant_rerank_scores else None
        ),
        "best_hard_negative_rerank_score": (
            max(hard_negative_rerank_scores) if hard_negative_rerank_scores else None
        ),
        "query_form": case.query_form,
        "relevant_branch_families": relevant_families,
        "translated_branch_contributed": bool(
            {"translated_dense", "translated_lexical"} & set(relevant_families)
        ),
        "candidate_union_relevant": relevant_retrieved,
        "translation_status": (search.provenance or {}).get("translation_status"),
        "translation_latency_ms": (search.provenance or {}).get("translation_latency_ms"),
        "reranker_latency_ms": (search.provenance or {}).get("reranker_latency_ms"),
        "executed_branches": (search.provenance or {}).get("executed_branches"),
        "filter_correct": filter_correct,
        "latency_ms": search.latency_ms,
        "rerank_status": search.rerank_status,
        "reranker_provider": search.reranker_provider,
        "reranker_model": search.reranker_model,
        "reranker_version": search.reranker_version,
        "reranker_score_scale": search.reranker_score_scale,
        "retrieval_provenance": search.provenance,
        "answer": answer.answer,
        "insufficient_evidence_reason": answer.insufficient_evidence_reason,
        "generation_ran": (
            bool(answer.generation_ran)
            if answer.generation_ran is not None
            else answer.insufficient_evidence_reason is None
        ),
        "evidence_gate": dict(answer.evidence_gate),
        "evidence_gate_mode": answer.evidence_gate.get("mode"),
        "evidence_gate_sufficient": answer.evidence_gate.get("sufficient"),
        "evidence_gate_reason": answer.evidence_gate.get("reason"),
        "evidence_score": answer.evidence_gate.get("evidence_score"),
        "evidence_score_method": answer.evidence_gate.get("evidence_score_method"),
        "query_token_coverage": answer.evidence_gate.get("query_token_coverage"),
        "lexically_corroborated": answer.evidence_gate.get("lexically_corroborated"),
        "winning_chunk_id": answer.evidence_gate.get("winning_chunk_id"),
        "winning_semantic_score": answer.evidence_gate.get("winning_semantic_score"),
        "winning_rank_score": answer.evidence_gate.get("winning_rank_score"),
        "grounded": answer.grounded,
        "citation_coverage": answer.citation_coverage,
        "claims": answer.claims,
        "unverified_claim_rate": _unverified_claim_rate(answer.claims),
        "answer_token_coverage": token_coverage,
        "provider": answer.provider,
        "model": answer.model,
        "input_tokens": answer.input_tokens,
        "output_tokens": answer.output_tokens,
        "provider_latency_ms": answer.provider_latency_ms,
    }


def _relevant_in_selected(
    relevant_ids: set[str],
    selected_chunk_ids: list[uuid.UUID],
    *,
    retrieved: bool,
) -> bool:
    selected = {str(chunk_id) for chunk_id in selected_chunk_ids}
    if not selected:
        return retrieved
    return bool(relevant_ids & selected)


def _relevant_dropped_before_gate(
    relevant_ids: set[str],
    result_ids: list[str],
    selected_chunk_ids: list[uuid.UUID],
) -> bool:
    selected = {str(chunk_id) for chunk_id in selected_chunk_ids}
    if not selected:
        return False
    retrieved = bool(relevant_ids & set(result_ids))
    return retrieved and not bool(relevant_ids & selected)


def _hit_branch_families(hit: Any) -> set[str]:
    contributions = (hit.metadata or {}).get("rrf_contributions")
    if not isinstance(contributions, list):
        return set()
    families: set[str] = set()
    for item in contributions:
        if isinstance(item, dict) and item.get("family"):
            families.add(str(item["family"]))
    return families


def _matches_evidence_phrases(content: str, phrases: list[str]) -> bool:
    normalized_content = normalize_for_indexing(content)
    return any(
        normalized_phrase in normalized_content
        for phrase in phrases
        if (normalized_phrase := normalize_for_indexing(phrase))
    )


def _complete_sum(rows: list[dict[str, Any]], field: str) -> int | None:
    if not rows:
        return None
    total = 0
    for row in rows:
        value = row.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        total += value
    return total


def _unverified_claim_rate(claims: list[dict[str, Any]]) -> float:
    if not claims:
        return 0.0
    return sum(claim.get("verification") == "unverified" for claim in claims) / len(claims)


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
        "rank_1_accuracy",
        "mrr",
        "ndcg",
        "filtered_correctness",
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
    for metric in (
        "false_refusal_rate",
        "false_accept_rate",
        "accepted_without_relevant_evidence_rate",
    ):
        if metric not in previous or metric not in current:
            continue
        delta = float(current[metric]) - float(previous[metric])
        if delta > tolerance:
            regressions.append(
                {
                    "metric": metric,
                    "previous": previous[metric],
                    "current": current[metric],
                    "delta": delta,
                }
            )
    return regressions


def _acceptance_failures(
    metrics: dict[str, Any],
    config: EvaluationConfig,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    checks: list[tuple[str, float, str]] = [
        ("recall_at_k", config.minimum_recall_at_k, "minimum"),
        ("rank_1_accuracy", config.minimum_rank_1_accuracy, "minimum"),
        ("filtered_correctness", config.minimum_filtered_correctness, "minimum"),
        ("false_refusal_rate", config.maximum_false_refusal_rate, "maximum"),
        ("false_accept_rate", config.maximum_false_accept_rate, "maximum"),
        (
            "accepted_without_relevant_evidence_rate",
            config.maximum_accepted_without_relevant_evidence_rate,
            "maximum",
        ),
        ("groundedness", config.minimum_groundedness, "minimum"),
        ("citation_coverage", config.minimum_citation_coverage, "minimum"),
        ("latency_p95_ms", config.maximum_p95_latency_ms, "maximum"),
    ]
    language_pairs = metrics.get("language_pairs", {})
    cross_pair_recalls = [
        float(pair_metrics["recall_at_k"])
        for pair, pair_metrics in language_pairs.items()
        if pair.split("->", maxsplit=1)[0] != pair.split("->", maxsplit=1)[-1]
    ]
    if cross_pair_recalls:
        checks.append(
            (
                "minimum_language_pair_recall_at_k",
                config.minimum_cross_lingual_recall_at_k,
                "minimum",
            )
        )
        metrics = {**metrics, "minimum_language_pair_recall_at_k": min(cross_pair_recalls)}
    for metric, threshold, direction in checks:
        if metric not in metrics:
            continue
        value = float(metrics[metric])
        failed = value < threshold if direction == "minimum" else value > threshold
        if failed:
            failures.append(
                {
                    "metric": metric,
                    "current": value,
                    "threshold": threshold,
                    "type": "acceptance_threshold",
                    "direction": direction,
                }
            )
    return failures


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
                "operational_fit": latency_penalty <= config.maximum_reranker_latency_penalty_ms,
                "eligible_for_promotion": candidate_eligible,
            }
        )
    eligible_candidates = [item for item in comparisons if item["eligible_for_promotion"]]
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
