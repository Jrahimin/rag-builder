"""Dataset lifecycle, durable run submission, and quality read model."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import EvaluationConfig
from app.core.exceptions import ConflictError, NotFoundError
from app.models.evaluation_dataset import EvaluationDataset
from app.models.evaluation_run import EvaluationRun
from app.modules.evaluation.repositories.evaluation_corpus_repository import (
    EvaluationCorpusRepository,
)
from app.modules.evaluation.repositories.evaluation_dataset_repository import (
    EvaluationDatasetRepository,
)
from app.modules.evaluation.repositories.evaluation_run_repository import (
    EvaluationRunRecord,
    EvaluationRunRepository,
)
from app.modules.evaluation.schemas.evaluation import (
    EvaluationDatasetCreate,
    EvaluationDatasetResponse,
    EvaluationRunCreate,
    EvaluationRunResponse,
    QualitySummary,
)
from app.platform.jobs.contracts import (
    DurableJobSubmitter,
    JobConfiguration,
    JobDefinition,
    JobSubmission,
    RetryPolicy,
)
from app.platform.jobs.names import EVALUATION_RUN


class EvaluationService:
    """Own immutable datasets and stage reproducible quality runs."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: uuid.UUID,
        submitter: DurableJobSubmitter,
        job_configuration: JobConfiguration,
        config: EvaluationConfig,
        version_snapshot: dict[str, Any],
        job_max_attempts: int,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._submitter = submitter
        self._job_configuration = job_configuration
        self._config = config
        self._version_snapshot = version_snapshot
        self._job_max_attempts = job_max_attempts
        self._datasets = EvaluationDatasetRepository(session, project_id)
        self._runs = EvaluationRunRepository(session, project_id)
        self._corpus = EvaluationCorpusRepository(session, project_id)

    async def create_dataset(self, request: EvaluationDatasetCreate) -> EvaluationDataset:
        if len(request.cases) > self._config.max_cases_per_dataset:
            raise ConflictError(
                message="Evaluation dataset exceeds the configured case limit.",
                code="evaluation_dataset_too_large",
            )
        existing = await self._datasets.get_by_name_version(request.name, request.version)
        if existing is not None:
            raise ConflictError(
                message="That evaluation dataset version already exists.",
                code="evaluation_dataset_version_exists",
            )
        case_payload = [case.model_dump(mode="json") for case in request.cases]
        dataset = EvaluationDataset(
            project_id=self._project_id,
            name=request.name.strip(),
            version=request.version.strip(),
            schema_version=request.schema_version,
            description=request.description,
            dataset_hash=_digest(case_payload),
            cases=case_payload,
        )
        self._datasets.add(dataset)
        await self._session.commit()
        await self._session.refresh(dataset)
        return dataset

    async def list_datasets(self, *, limit: int, offset: int) -> list[EvaluationDataset]:
        return await self._datasets.list_latest(limit=limit, offset=offset)

    async def queue_run(
        self,
        request: EvaluationRunCreate,
    ) -> tuple[EvaluationRunResponse, JobSubmission]:
        dataset = await self._datasets.get_by_id(request.dataset_id)
        if dataset is None:
            raise NotFoundError(
                message="Evaluation dataset not found.",
                code="evaluation_dataset_not_found",
            )
        top_k = request.top_k or self._config.default_top_k
        corpus = await self._corpus.snapshot(
            embedding_set_version=int(
                self._version_snapshot["retrieval"]["embedding_set_version"]
            ),
            embedding_provider=str(self._version_snapshot["embedding"]["backend"]),
            embedding_model=str(self._version_snapshot["embedding"]["model"]),
        )
        versions = {
            **self._version_snapshot,
            "corpus": corpus,
            "dataset": {
                "id": str(dataset.id),
                "name": dataset.name,
                "version": dataset.version,
                "schema_version": dataset.schema_version,
                "hash": dataset.dataset_hash,
            },
            "evaluation": {
                "evaluator_version": self._config.evaluator_version,
                "top_k": top_k,
            },
        }
        run = EvaluationRun(
            project_id=self._project_id,
            dataset_id=dataset.id,
            top_k=top_k,
            configuration_hash=_digest(versions),
            versions=versions,
            metrics={},
            case_results=[],
            regressions=[],
            failed_cases=[],
            reranker_comparison={},
        )
        self._runs.add(run)
        await self._runs.flush()
        submission = await self._submitter.stage(
            JobDefinition(
                name=EVALUATION_RUN,
                project_id=self._project_id,
                payload_version=1,
                payload={"evaluation_run_id": str(run.id)},
                idempotency_key=f"evaluation.run:{run.id}",
                retry=RetryPolicy(max_attempts=self._job_max_attempts),
            ),
            self._job_configuration,
        )
        run.job_id = submission.job_id
        await self._runs.flush()
        await self._session.commit()
        await self._session.refresh(run)
        await self._submitter.dispatch(submission.job_id)
        record = await self._runs.get_record(run.id)
        if record is None:  # pragma: no cover - protected by transaction invariants
            msg = "Evaluation run did not retain its durable job"
            raise RuntimeError(msg)
        return _run_response(record), submission

    async def list_runs(self, *, limit: int, offset: int) -> list[EvaluationRunResponse]:
        return [
            _run_response(record)
            for record in await self._runs.list_records(limit=limit, offset=offset)
        ]

    async def get_run(self, run_id: uuid.UUID) -> EvaluationRunResponse:
        record = await self._runs.get_record(run_id)
        if record is None:
            raise NotFoundError(
                message="Evaluation run not found.",
                code="evaluation_run_not_found",
            )
        return _run_response(record)

    async def quality_summary(self) -> QualitySummary:
        run = await self._runs.latest_record()
        dataset = (
            await self._datasets.get_by_id(run.run.dataset_id)
            if run is not None
            else await self._datasets.latest()
        )
        return QualitySummary(
            dataset=(
                EvaluationDatasetResponse.model_validate(dataset) if dataset is not None else None
            ),
            last_run=_run_response(run) if run is not None else None,
            acceptance_thresholds={
                "minimum_recall_at_k": self._config.minimum_recall_at_k,
                "minimum_filtered_correctness": self._config.minimum_filtered_correctness,
                "minimum_refusal_accuracy": self._config.minimum_refusal_accuracy,
                "minimum_groundedness": self._config.minimum_groundedness,
                "minimum_citation_coverage": self._config.minimum_citation_coverage,
                "maximum_p95_latency_ms": self._config.maximum_p95_latency_ms,
                "maximum_metric_regression": self._config.maximum_metric_regression,
                "minimum_reranker_ndcg_gain": self._config.minimum_reranker_ndcg_gain,
                "maximum_reranker_latency_penalty_ms": (
                    self._config.maximum_reranker_latency_penalty_ms
                ),
            },
        )


def _run_response(record: EvaluationRunRecord) -> EvaluationRunResponse:
    run = record.run
    if run.job_id is None:  # pragma: no cover - only visible inside submission transaction
        msg = "Evaluation run has no durable job"
        raise RuntimeError(msg)
    return EvaluationRunResponse(
        id=run.id,
        project_id=run.project_id,
        dataset_id=run.dataset_id,
        job_id=run.job_id,
        job_state=record.job.state.value,
        top_k=run.top_k,
        configuration_hash=run.configuration_hash,
        versions=dict(run.versions),
        metrics=dict(run.metrics),
        case_results=list(run.case_results),
        regressions=list(run.regressions),
        failed_cases=list(run.failed_cases),
        reranker_comparison=dict(run.reranker_comparison),
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
