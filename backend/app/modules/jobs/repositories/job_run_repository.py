"""Project-scoped durable job-run persistence and lease transitions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.job_run import JobRun, JobState, JobType
from app.platform.jobs.contracts import JobDefinition
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class JobRunRepository(ProjectScopedRepository[JobRun]):
    """Durable job access with atomic lease acquisition and fencing."""

    model = JobRun

    async def create_if_absent(
        self,
        job: JobDefinition,
        *,
        configuration_snapshot_id: uuid.UUID,
        retry_of_job_id: uuid.UUID | None = None,
    ) -> tuple[JobRun, bool]:
        run_id = uuid.uuid4()
        stmt = (
            insert(JobRun)
            .values(
                id=run_id,
                project_id=self._project_id,
                job_type=JobType(job.name),
                state=JobState.QUEUED,
                stage="queued",
                progress=0,
                payload=job.payload,
                idempotency_key=job.idempotency_key or str(run_id),
                document_id=job.document_id,
                configuration_snapshot_id=configuration_snapshot_id,
                retry_of_job_id=retry_of_job_id,
                attempt_count=0,
                max_attempts=job.retry.max_attempts,
            )
            .on_conflict_do_nothing(index_elements=["project_id", "idempotency_key"])
            .returning(JobRun.id)
        )
        inserted_id = (await self._session.execute(stmt)).scalar_one_or_none()
        created = inserted_id is not None
        if inserted_id is None:
            result = await self._session.execute(
                self._scoped().where(JobRun.idempotency_key == (job.idempotency_key or str(run_id)))
            )
            run = result.scalar_one()
        else:
            inserted_run = await self.get_by_id(inserted_id)
            if inserted_run is None:  # pragma: no cover - database invariant
                msg = "Job upsert did not produce a row"
                raise RuntimeError(msg)
            run = inserted_run
        return run, created

    async def list_filtered(
        self,
        *,
        limit: int,
        offset: int,
        state: JobState | None = None,
        job_type: JobType | None = None,
        document_id: uuid.UUID | None = None,
    ) -> list[JobRun]:
        stmt = self._scoped()
        if state is not None:
            stmt = stmt.where(JobRun.state == state)
        if job_type is not None:
            stmt = stmt.where(JobRun.job_type == job_type)
        if document_id is not None:
            stmt = stmt.where(JobRun.document_id == document_id)
        stmt = stmt.order_by(JobRun.created_at.desc(), JobRun.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_filtered(
        self,
        *,
        state: JobState | None = None,
        job_type: JobType | None = None,
        document_id: uuid.UUID | None = None,
    ) -> int:
        clauses = [JobRun.project_id == self._project_id]
        if state is not None:
            clauses.append(JobRun.state == state)
        if job_type is not None:
            clauses.append(JobRun.job_type == job_type)
        if document_id is not None:
            clauses.append(JobRun.document_id == document_id)
        result = await self._session.execute(
            select(func.count()).select_from(JobRun).where(*clauses)
        )
        return int(result.scalar_one())

    async def acquire(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> JobRun | None:
        now = func.now()
        eligible = or_(
            JobRun.state == JobState.QUEUED,
            and_(
                JobRun.state == JobState.RETRY_SCHEDULED,
                or_(JobRun.next_attempt_at.is_(None), JobRun.next_attempt_at <= now),
            ),
            and_(
                JobRun.state == JobState.RUNNING,
                JobRun.lease_expires_at.is_not(None),
                JobRun.lease_expires_at < now,
            ),
        )
        stmt = (
            update(JobRun)
            .where(
                JobRun.id == job_id,
                JobRun.project_id == self._project_id,
                eligible,
                JobRun.attempt_count < JobRun.max_attempts,
            )
            .values(
                state=JobState.RUNNING,
                stage="starting",
                attempt_count=JobRun.attempt_count + 1,
                started_at=func.coalesce(JobRun.started_at, now),
                heartbeat_at=now,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                next_attempt_at=None,
                completed_at=None,
            )
            .returning(JobRun)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def heartbeat(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        stage: str | None = None,
        progress: int | None = None,
    ) -> bool:
        values: dict[str, object] = {
            "heartbeat_at": func.now(),
            "lease_expires_at": func.now() + timedelta(seconds=lease_seconds),
        }
        if stage is not None:
            values["stage"] = stage
        if progress is not None:
            values["progress"] = progress
        stmt = (
            update(JobRun)
            .where(
                JobRun.id == job_id,
                JobRun.project_id == self._project_id,
                JobRun.state == JobState.RUNNING,
                JobRun.lease_owner == worker_id,
                JobRun.lease_expires_at.is_not(None),
                JobRun.lease_expires_at > func.now(),
            )
            .values(**values)
        )
        result = await self._session.execute(stmt)
        return bool(getattr(result, "rowcount", 0))

    async def lock_owned_run(self, job_id: uuid.UUID, *, worker_id: str) -> JobRun | None:
        result = await self._session.execute(
            self._scoped()
            .where(
                JobRun.id == job_id,
                JobRun.state == JobState.RUNNING,
                JobRun.lease_owner == worker_id,
                JobRun.lease_expires_at.is_not(None),
                JobRun.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_expired_for_update(self, *, limit: int) -> list[JobRun]:
        result = await self._session.execute(
            self._scoped()
            .where(
                JobRun.state == JobState.RUNNING,
                JobRun.lease_expires_at.is_not(None),
                JobRun.lease_expires_at < func.now(),
            )
            .order_by(JobRun.lease_expires_at, JobRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    @staticmethod
    def schedule_retry(run: JobRun, *, available_at: datetime, code: str, message: str) -> None:
        run.state = JobState.RETRY_SCHEDULED
        run.stage = "retry_scheduled"
        run.next_attempt_at = available_at
        run.lease_owner = None
        run.lease_expires_at = None
        run.failure_code = code
        run.failure_message = message
        run.failure_details = {"retryable": True}

    @staticmethod
    def mark_failed(
        run: JobRun,
        *,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        run.state = JobState.FAILED
        run.stage = "failed"
        run.completed_at = datetime.now(UTC)
        run.lease_owner = None
        run.lease_expires_at = None
        run.failure_code = code
        run.failure_message = message
        run.failure_details = details

    @staticmethod
    def mark_succeeded(run: JobRun) -> None:
        run.state = JobState.SUCCEEDED
        run.stage = "completed"
        run.progress = 100
        run.completed_at = datetime.now(UTC)
        run.lease_owner = None
        run.lease_expires_at = None
        run.next_attempt_at = None
        run.failure_code = None
        run.failure_message = None
        run.failure_details = None
