"""Integration coverage for durable dispatch, recovery, replay, and job APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import get_settings
from app.models.document_chunk import DocumentChunk
from app.models.job_outbox import JobOutbox, JobOutboxState
from app.models.job_run import JobRun, JobState
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from tests.conftest import CapturingJobQueue
from tests.integration.knowledge_helpers import run_captured_document_jobs

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"Jobs {uuid.uuid4().hex[:10]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


class _FailingQueue(JobQueue):
    async def enqueue(self, job: JobDefinition) -> str:
        del job
        raise JobEnqueueError("redis unavailable")


async def test_job_api_is_project_scoped_and_retry_returns_new_identity(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    other_project_id = await _create_project(db_client)
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("job.txt", b"valid job", "text/plain")},
    )
    assert upload.status_code == 201
    job_id = upload.json()["data"]["job_id"]
    assert job_id

    listed = await db_client.get(f"/api/v1/projects/{project_id}/jobs")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["id"] == job_id

    detail = await db_client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["configuration_hash"]
    assert "openai_api_key" not in str(detail.json()["data"]["configuration"])

    cross_project = await db_client.get(f"/api/v1/projects/{other_project_id}/jobs/{job_id}")
    assert cross_project.status_code == 404
    assert cross_project.json()["error"]["code"] == "job_not_found"

    await integration_connection.execute(
        update(JobRun)
        .where(JobRun.id == uuid.UUID(job_id))
        .values(
            state=JobState.FAILED,
            failure_code="test_failure",
            failure_message="forced integration failure",
            failure_details={"retryable": False},
            completed_at=datetime.now(UTC),
        )
    )
    captured_jobs.clear()
    failed = await db_client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")
    failed_data = failed.json()["data"]
    assert failed_data["state"] == "failed"
    assert failed_data["failure_code"] == "test_failure"
    assert failed_data["failure_details"]["retryable"] is False

    retry = await db_client.post(f"/api/v1/projects/{project_id}/jobs/{job_id}/retry")
    assert retry.status_code == 200
    retry_data = retry.json()["data"]
    assert retry_data["id"] != job_id
    assert retry_data["retry_of_job_id"] == job_id
    assert retry_data["state"] == "queued"
    assert len(captured_jobs) == 1


async def test_committed_outbox_survives_redis_failure_and_later_dispatches(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = uuid.UUID(await _create_project(db_client))
    settings = get_settings()
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        service = build_job_service(
            session=session,
            project_id=project_id,
            settings=settings,
            queue=_FailingQueue(),
        )
        submission = await service.stage(
            JobDefinition(
                name="document.process",
                project_id=project_id,
                payload={"document_version": 1},
                idempotency_key=f"outbox-failure:{uuid.uuid4()}",
            ),
            build_job_configuration(settings),
        )
        await session.commit()
        await service.dispatch(submission.job_id)

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        run = await session.get(JobRun, submission.job_id)
        outbox = (
            await session.execute(
                select(JobOutbox).where(JobOutbox.job_run_id == submission.job_id)
            )
        ).scalar_one()
        assert run is not None and run.state is JobState.QUEUED
        assert outbox.state is JobOutboxState.PENDING
        assert outbox.dispatch_attempts == 1
        assert outbox.last_error
        outbox.available_at = datetime.now(UTC)
        await session.commit()

        recovered = build_job_service(
            session=session,
            project_id=project_id,
            settings=settings,
            queue=CapturingJobQueue(captured_jobs),
        )
        assert await recovered.dispatch_next() is True

    assert len(captured_jobs) == 1
    assert captured_jobs[0].payload == {"job_id": str(submission.job_id)}


async def test_expired_lease_replays_process_outputs_without_duplicates(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("recover.txt", b"lease recovery replay safety", "text/plain")},
    )
    assert upload.status_code == 201
    document_id = uuid.UUID(upload.json()["data"]["id"])
    job_id = uuid.UUID(upload.json()["data"]["job_id"])
    delivery = captured_jobs.pop()
    settings = get_settings()
    project_uuid = uuid.UUID(project_id)

    # Simulate a worker that committed stage outputs and died before marking
    # the durable run succeeded.
    from app.platform.jobs.configuration import apply_job_configuration
    from app.platform.jobs.contracts import JobConfiguration
    from app.worker.handlers.document import _process

    class _Reporter:
        async def report(self, stage: str, progress: int) -> None:
            del stage, progress

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        service = build_job_service(
            session=session,
            project_id=project_uuid,
            settings=settings,
            queue=CapturingJobQueue(captured_jobs),
        )
        run = await service.acquire(job_id, worker_id="crashed-worker")
        assert run is not None
        detail = await service.get_detail(job_id)
        snapshot = JobConfiguration.model_validate(detail.configuration.configuration)
        effective = apply_job_configuration(settings, snapshot)
        await _process(session, run, effective, service, _Reporter())  # type: ignore[arg-type]

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        before = await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.project_id == project_uuid,
                DocumentChunk.document_id == document_id,
            )
        )
        assert before and before > 0
        await session.execute(
            update(JobRun)
            .where(JobRun.id == job_id, JobRun.project_id == project_uuid)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        service = build_job_service(
            session=session,
            project_id=project_uuid,
            settings=settings,
            queue=CapturingJobQueue(captured_jobs),
        )
        recovery = await service.recover_expired(limit=10)
        assert recovery.rescheduled == 1
        await session.commit()
        assert await service.dispatch_next() is True

    assert captured_jobs and captured_jobs[0].name == delivery.name
    await run_captured_document_jobs(integration_connection, captured_jobs)

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        after = await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.project_id == project_uuid,
                DocumentChunk.document_id == document_id,
            )
        )
        distinct_indexes = await session.scalar(
            select(func.count(func.distinct(DocumentChunk.chunk_index))).where(
                DocumentChunk.project_id == project_uuid,
                DocumentChunk.document_id == document_id,
            )
        )
        run = await session.get(JobRun, job_id)
        assert after == before == distinct_indexes
        assert run is not None and run.state is JobState.SUCCEEDED
        assert run.attempt_count == 2
