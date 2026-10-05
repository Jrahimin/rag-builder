"""Real durable worker suspension/resumption, including restart and duplicate deliveries."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.composition.jobs import DurableJobDispatcher
from app.core.config import get_settings
from app.models.document import Document
from app.models.index_build import IndexBuild
from app.models.job_run import JobRun, JobState, JobType
from app.worker.handlers.document_lifecycle import _delete, _purge
from app.worker.job_runtime import run_durable_job
from tests.integration.build_acceptance_helpers import attest_fixture_build
from tests.integration.knowledge_helpers import _CaptureQueue
from tests.integration.test_index_lifecycle_api import _ready_document

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("purge", [False, True])
async def test_real_worker_pending_activation_resumes_once_after_restart(
    db_client, integration_connection, captured_jobs, monkeypatch, purge
):
    project, document = await _ready_document(db_client, integration_connection, captured_jobs)
    document_id = uuid.UUID(document)
    settings = get_settings()
    factory = async_sessionmaker(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    queue = _CaptureQueue(captured_jobs)

    class IsolatedDatabase:
        def __init__(self, settings):
            self.session_factory = factory

        async def dispose(self):
            pass

    monkeypatch.setattr("app.worker.job_runtime.Database", IsolatedDatabase)
    monkeypatch.setattr("app.worker.job_runtime.create_job_queue", lambda settings: queue)
    path = f"/api/v1/projects/{project}/documents/{document}"
    response = await db_client.delete(path + ("/purge" if purge else ""))
    assert response.status_code in {200, 202, 204}, response.text
    job_type = JobType.DOCUMENT_PURGE if purge else JobType.DOCUMENT_DELETE
    async with factory() as session:
        run = await session.scalar(
            select(JobRun).where(
                JobRun.project_id == uuid.UUID(project),
                JobRun.document_id == document_id,
                JobRun.job_type == job_type,
            )
        )
        assert run is not None
        run_id = run.id
    operation = _purge if purge else _delete

    async def delivery():
        await run_durable_job(
            project_id=project, job_id=run_id, expected_type=job_type, operation=operation
        )

    await delivery()
    async with factory() as session:
        run = await session.get(JobRun, run_id)
        assert run.state is JobState.WAITING_ACCEPTANCE and run.completed_at is None
        assert run.result["destructive_work"] == "not_started"
        build_id = uuid.UUID(run.payload["build_id"])
        original = await session.get(Document, document_id)
        assert original is not None and original.deleted_at is None
    await delivery()  # Duplicate delivery cannot acquire suspended work or destroy anything.
    async with factory() as session:
        assert (await session.get(JobRun, run_id)).state is JobState.WAITING_ACCEPTANCE
        await attest_fixture_build(session, uuid.UUID(project), build_id)
        build = await session.get(IndexBuild, build_id)
        assert build is not None
    # Acceptance plus the real API activation makes the persisted continuation eligible.
    activation = await db_client.post(
        f"/api/v1/projects/{project}/index-builds/{build_id}/activate"
    )
    assert activation.status_code == 200, activation.text
    captured_jobs.clear()
    restarted_dispatcher = DurableJobDispatcher(
        session_factory=factory, settings=settings, queue=queue
    )
    assert await restarted_dispatcher.run_once() >= 1
    assert any(item.payload["job_id"] == str(run_id) for item in captured_jobs)
    assert await restarted_dispatcher.run_once() == 0
    await delivery()
    await delivery()  # Completed delivery remains idempotent.
    async with factory() as session:
        run = await session.get(JobRun, run_id)
        assert run.state is JobState.SUCCEEDED and run.completed_at is not None
        assert run.result["mode"] == ("purge" if purge else "delete")
        original = await session.get(Document, document_id)
        assert (
            original is None if purge else original is not None and original.deleted_at is not None
        )
