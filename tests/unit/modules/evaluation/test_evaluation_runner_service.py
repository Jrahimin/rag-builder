"""Evaluation runner comparison, persistence, and corpus-isolation tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.config import EvaluationConfig
from app.modules.evaluation.errors import EvaluationCorpusChangedError
from app.modules.evaluation.ports import QualityAnswer, QualityHit, QualitySearchResult
from app.modules.evaluation.services.evaluation_runner_service import EvaluationRunnerService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_RELEVANT = uuid.UUID("10000000-0000-0000-0000-000000000001")
_IRRELEVANT = uuid.UUID("10000000-0000-0000-0000-000000000099")
_DOCUMENT = uuid.UUID("20000000-0000-0000-0000-000000000001")
_CORPUS = {
    "fingerprint": "a" * 64,
    "embedding_set_version": 1,
    "embedding_provider": "hash",
    "embedding_model": "test",
}


class _Runs:
    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run
        self.flushed = False

    async def get_by_id(self, run_id: uuid.UUID) -> SimpleNamespace | None:
        return self.run if run_id == self.run.id else None

    async def latest_completed_before(self, **_: object) -> None:
        return None

    async def flush(self) -> None:
        self.flushed = True


class _Datasets:
    def __init__(self, dataset: SimpleNamespace) -> None:
        self.dataset = dataset

    async def get_by_id(self, dataset_id: uuid.UUID) -> SimpleNamespace | None:
        return self.dataset if dataset_id == self.dataset.id else None


class _Corpus:
    def __init__(self, fingerprint: str = "a" * 64) -> None:
        self.fingerprint = fingerprint

    async def snapshot(self, **_: object) -> dict[str, object]:
        return {**_CORPUS, "fingerprint": self.fingerprint}


class _Retrieval:
    profiles = ("semantic", "hybrid", "reranked_embedding")
    primary_profile = "reranked_embedding"
    profile_metadata: ClassVar[dict[str, dict[str, object]]] = {
        "semantic": {"learned": False},
        "hybrid": {"learned": False},
        "reranked_embedding": {
            "learned": True,
            "provider": "test",
            "model": "cross-encoder-test",
            "version": "1",
        },
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, uuid.UUID | None, dict[str, str]]] = []

    async def search(
        self,
        *,
        profile: str,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None,
        metadata_filter: dict[str, str],
    ) -> QualitySearchResult:
        del top_k
        self.calls.append((profile, query, document_id, metadata_filter))
        if query == "unanswerable":
            hits: list[QualityHit] = []
        else:
            relevant = _hit(_RELEVANT, "cobalt policy")
            irrelevant = _hit(_IRRELEVANT, "unrelated material")
            hits = {
                "semantic": [irrelevant],
                "hybrid": [irrelevant, relevant],
                "reranked_embedding": [relevant, irrelevant],
            }[profile]
        return QualitySearchResult(
            hits=hits,
            latency_ms={"semantic": 8, "hybrid": 10, "reranked_embedding": 12}[profile],
            rerank_status="applied" if profile.startswith("reranked_") else "disabled",
            reranker_provider="test" if profile.startswith("reranked_") else None,
            reranker_model=("cross-encoder-test" if profile.startswith("reranked_") else None),
            reranker_version="1" if profile.startswith("reranked_") else None,
        )


class _Answerer:
    async def answer(self, *, question: str, hits: list[QualityHit]) -> QualityAnswer:
        del question
        if not hits:
            return QualityAnswer(
                answer="insufficient",
                insufficient_evidence_reason="no_retrieval_results",
                grounded=False,
                citation_coverage=1.0,
                claims=[],
            )
        return QualityAnswer(
            answer="cobalt [1]",
            insufficient_evidence_reason=None,
            grounded=True,
            citation_coverage=1.0,
            claims=[{"text": "cobalt", "evidence": [{"chunk_id": str(hits[0].chunk_id)}]}],
        )


def _hit(chunk_id: uuid.UUID, content: str) -> QualityHit:
    return QualityHit(
        chunk_id=chunk_id,
        document_id=_DOCUMENT,
        content=content,
        score=0.9,
        filename="policy.txt",
        chunk_index=0,
    )


def _run_and_dataset() -> tuple[SimpleNamespace, SimpleNamespace]:
    dataset_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        dataset_id=dataset_id,
        top_k=5,
        versions={"corpus": dict(_CORPUS)},
        metrics={},
        case_results=[],
        regressions=[],
        failed_cases=[],
        reranker_comparison={},
        completed_at=None,
    )
    dataset = SimpleNamespace(
        id=dataset_id,
        cases=[
            {
                "key": "exact",
                "kind": "exact_token",
                "query": "cobalt",
                "relevant_chunk_ids": [str(_RELEVANT)],
                "document_id": str(_DOCUMENT),
                "metadata_filter": {"source": "policy"},
                "expected_answer_tokens": ["cobalt"],
            },
            {
                "key": "no-answer",
                "kind": "no_answer",
                "query": "unanswerable",
                "expected_no_answer": True,
            },
        ],
    )
    return run, dataset


async def test_runner_measures_hybrid_and_reranker_improvement_on_identical_cases() -> None:
    run, dataset = _run_and_dataset()
    runs = _Runs(run)
    retrieval = _Retrieval()
    runner = EvaluationRunnerService(
        runs=runs,  # type: ignore[arg-type]
        datasets=_Datasets(dataset),  # type: ignore[arg-type]
        corpus=_Corpus(),  # type: ignore[arg-type]
        retrieval=retrieval,
        answerer=_Answerer(),
        config=EvaluationConfig(),
    )

    await runner.run(run.id)

    assert run.metrics["hybrid"]["ndcg"] > run.metrics["semantic"]["ndcg"]
    assert run.metrics["reranked_embedding"]["ndcg"] > run.metrics["hybrid"]["ndcg"]
    assert run.reranker_comparison["recommended_profile"] == "reranked_embedding"
    assert run.failed_cases == []
    assert runs.flushed is True
    exact_calls = [call for call in retrieval.calls if call[1] == "cobalt"]
    assert len(exact_calls) == 3
    assert {call[2] for call in exact_calls} == {_DOCUMENT}
    assert {tuple(call[3].items()) for call in exact_calls} == {(("source", "policy"),)}


async def test_runner_rejects_corpus_change_before_any_query() -> None:
    run, dataset = _run_and_dataset()
    retrieval = _Retrieval()
    runner = EvaluationRunnerService(
        runs=_Runs(run),  # type: ignore[arg-type]
        datasets=_Datasets(dataset),  # type: ignore[arg-type]
        corpus=_Corpus("b" * 64),  # type: ignore[arg-type]
        retrieval=retrieval,
        answerer=_Answerer(),
        config=EvaluationConfig(),
    )

    with pytest.raises(EvaluationCorpusChangedError):
        await runner.run(run.id)

    assert retrieval.calls == []
