"""Unit tests for durable retry, recovery, and outbox transitions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import JobsConfig
from app.models.job_outbox import JobOutbox, JobOutboxState
from app.models.job_run import JobRun, JobState, JobType
from app.modules.jobs.services.job_service import JobService
from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.failure import JobFailure

pytestmark = pytest.mark.unit


def _run(*, attempt_count: int, max_attempts: int = 3) -> JobRun:
    return JobRun(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        job_type=JobType.DOCUMENT_PROCESS,
        state=JobState.RUNNING,
        stage="parsing",
        progress=25,
        payload={"document_version": 1},
        idempotency_key=str(uuid.uuid4()),
        configuration_snapshot_id=uuid.uuid4(),
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )


def _service(run: JobRun, queue: AsyncMock | None = None) -> JobService:
    session = AsyncMock()
    service = JobService(
        session=session,
        project_id=run.project_id,
        queue=queue or AsyncMock(spec=JobQueue),
        config=JobsConfig(),
    )
    service._runs.lock_owned_run = AsyncMock(return_value=run)
    service._outbox.add_intent = MagicMock()
    return service


async def test_transient_failure_schedules_durable_retry() -> None:
    run = _run(attempt_count=1)
    service = _service(run)

    failed_run, will_retry = await service.stage_failure(
        run.id,
        worker_id="worker",
        failure=JobFailure(
            code="provider_timeout_error",
            message="Provider timed out.",
            retryable=True,
            details={"provider": "test"},
        ),
    )

    assert will_retry is True
    assert failed_run.state is JobState.RETRY_SCHEDULED
    assert failed_run.next_attempt_at is not None
    assert failed_run.lease_owner is None
    service._outbox.add_intent.assert_called_once_with(
        run.id,
        available_at=failed_run.next_attempt_at,
    )


async def test_permanent_failure_is_terminal_and_structured() -> None:
    run = _run(attempt_count=1)
    service = _service(run)

    failed_run, will_retry = await service.stage_failure(
        run.id,
        worker_id="worker",
        failure=JobFailure(
            code="unsupported_document",
            message="Unsupported document type.",
            retryable=False,
            details={"content_type": "x/test"},
        ),
    )

    assert will_retry is False
    assert failed_run.state is JobState.FAILED
    assert failed_run.failure_code == "unsupported_document"
    assert failed_run.failure_details == {
        "content_type": "x/test",
        "retryable": False,
    }
    service._outbox.add_intent.assert_not_called()


async def test_expired_runs_are_rescheduled_or_exhausted() -> None:
    retryable = _run(attempt_count=1)
    exhausted = _run(attempt_count=3)
    exhausted.project_id = retryable.project_id
    service = _service(retryable)
    service._runs.list_expired_for_update = AsyncMock(return_value=[retryable, exhausted])

    result = await service.recover_expired(limit=10)

    assert result.rescheduled == 1
    assert result.failed == (exhausted,)
    assert retryable.state is JobState.RETRY_SCHEDULED
    assert retryable.failure_code == "job_lease_expired"
    assert exhausted.state is JobState.FAILED
    assert exhausted.failure_code == "job_attempts_exhausted"
    service._outbox.add_intent.assert_called_once()


async def test_dispatch_failure_keeps_outbox_pending() -> None:
    run = _run(attempt_count=0)
    queue = AsyncMock(spec=JobQueue)
    queue.enqueue.side_effect = RuntimeError("redis down")
    service = _service(run, queue)
    outbox = JobOutbox(
        id=uuid.uuid4(),
        project_id=run.project_id,
        job_run_id=run.id,
        state=JobOutboxState.PENDING,
        available_at=datetime.now(UTC),
        dispatch_attempts=0,
    )
    service._outbox.claim_pending = AsyncMock(return_value=outbox)
    service._runs.get_by_id = AsyncMock(return_value=run)

    assert await service.dispatch_next() is True

    assert outbox.state is JobOutboxState.PENDING
    assert outbox.dispatch_attempts == 1
    assert outbox.last_error == "RuntimeError: redis down"
    service._session.commit.assert_awaited_once()


async def test_dispatch_already_claimed_closes_read_without_expiring_response() -> None:
    run = _run(attempt_count=0)
    service = _service(run)
    service._outbox.claim_pending = AsyncMock(return_value=None)

    assert await service.dispatch(run.id) is None

    service._session.commit.assert_awaited_once()
    service._session.rollback.assert_not_awaited()


async def test_success_does_not_publish_child_for_superseded_document_version() -> None:
    run = _run(attempt_count=1)
    run.document_id = uuid.uuid4()
    service = _service(run)
    service._session.scalar.return_value = 2
    child = JobDefinition(
        name="document.embed",
        project_id=run.project_id,
        document_id=run.document_id,
        payload={"document_version": 1},
        idempotency_key="stale-child",
    )

    submission = await service.stage_success(
        run.id,
        worker_id="worker",
        child=child,
    )

    assert submission is None
    assert run.state is JobState.SUCCEEDED
    service._outbox.add_intent.assert_not_called()
