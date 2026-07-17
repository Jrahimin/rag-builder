"""Project-scoped transactional outbox persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func

from app.models.job_outbox import JobOutbox, JobOutboxState
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class JobOutboxRepository(ProjectScopedRepository[JobOutbox]):
    model = JobOutbox

    def add_intent(
        self,
        job_run_id: uuid.UUID,
        *,
        available_at: datetime | None = None,
    ) -> JobOutbox:
        row = JobOutbox(
            project_id=self._project_id,
            job_run_id=job_run_id,
            state=JobOutboxState.PENDING,
        )
        if available_at is not None:
            row.available_at = available_at
        self.add(row)
        return row

    async def claim_pending(
        self,
        *,
        job_run_id: uuid.UUID | None = None,
    ) -> JobOutbox | None:
        stmt = self._scoped().where(
            JobOutbox.state == JobOutboxState.PENDING,
            JobOutbox.available_at <= func.now(),
        )
        if job_run_id is not None:
            stmt = stmt.where(JobOutbox.job_run_id == job_run_id)
        stmt = stmt.order_by(JobOutbox.available_at, JobOutbox.created_at, JobOutbox.id)
        result = await self._session.execute(stmt.limit(1).with_for_update(skip_locked=True))
        return result.scalar_one_or_none()

    @staticmethod
    def mark_dispatched(row: JobOutbox, *, task_id: str) -> None:
        row.state = JobOutboxState.DISPATCHED
        row.dispatched_at = datetime.now(UTC)
        row.task_id = task_id
        row.last_error = None
        row.dispatch_attempts += 1

    @staticmethod
    def mark_dispatch_failed(
        row: JobOutbox,
        *,
        error: str,
        available_at: datetime,
    ) -> None:
        row.dispatch_attempts += 1
        row.last_error = error[:2000]
        row.available_at = available_at
