"""Project-scoped evaluation run persistence and job-state joins."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.models.evaluation_run import EvaluationRun
from app.models.job_run import JobRun, JobState
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


@dataclass(frozen=True, slots=True)
class EvaluationRunRecord:
    run: EvaluationRun
    job: JobRun


class EvaluationRunRepository(ProjectScopedRepository[EvaluationRun]):
    model = EvaluationRun

    async def get_record(self, run_id: uuid.UUID) -> EvaluationRunRecord | None:
        result = await self._session.execute(
            select(EvaluationRun, JobRun)
            .join(JobRun, JobRun.id == EvaluationRun.job_id)
            .where(
                EvaluationRun.project_id == self._project_id,
                EvaluationRun.id == run_id,
                JobRun.project_id == self._project_id,
            )
        )
        row = result.one_or_none()
        return EvaluationRunRecord(run=row[0], job=row[1]) if row is not None else None

    async def list_records(self, *, limit: int, offset: int) -> list[EvaluationRunRecord]:
        result = await self._session.execute(
            select(EvaluationRun, JobRun)
            .join(JobRun, JobRun.id == EvaluationRun.job_id)
            .where(
                EvaluationRun.project_id == self._project_id,
                JobRun.project_id == self._project_id,
            )
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [EvaluationRunRecord(run=row[0], job=row[1]) for row in result.all()]

    async def latest_record(self) -> EvaluationRunRecord | None:
        rows = await self.list_records(limit=1, offset=0)
        return rows[0] if rows else None

    async def latest_completed_before(
        self,
        *,
        dataset_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> EvaluationRun | None:
        return await self._session.scalar(
            select(EvaluationRun)
            .join(JobRun, JobRun.id == EvaluationRun.job_id)
            .where(
                EvaluationRun.project_id == self._project_id,
                EvaluationRun.dataset_id == dataset_id,
                EvaluationRun.id != run_id,
                EvaluationRun.completed_at.is_not(None),
                JobRun.project_id == self._project_id,
                JobRun.state == JobState.SUCCEEDED,
            )
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(1)
        )
