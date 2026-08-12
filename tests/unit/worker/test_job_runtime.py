"""Regression coverage for durable worker failure handling."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.models.job_run import JobType
from app.platform.providers.errors import ProviderAuthenticationError
from app.worker import job_runtime

pytestmark = pytest.mark.unit


class _RunThatExpiresOnRollback:
    """Raises if the runtime reads its ORM identity after rollback."""

    def __init__(self, run_id: uuid.UUID, project_id: uuid.UUID) -> None:
        self._id = run_id
        self._expired = False
        self.project_id = project_id
        self.job_type = JobType.DOCUMENT_PROCESS

    @property
    def id(self) -> uuid.UUID:
        if self._expired:
            raise AssertionError("run.id was accessed after rollback")
        return self._id


async def test_provider_failure_is_recorded_without_reading_expired_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = _RunThatExpiresOnRollback(run_id, project_id)
    session = AsyncMock()
    session.expunge = MagicMock()

    async def rollback() -> None:
        run._expired = True

    session.rollback.side_effect = rollback

    @asynccontextmanager
    async def session_scope():
        yield session

    database = MagicMock()
    database.session_factory.side_effect = session_scope
    database.dispose = AsyncMock()

    failed_run = SimpleNamespace(document_id=None, payload={})
    service = MagicMock()
    service.acquire = AsyncMock(return_value=run)
    service.get_detail = AsyncMock(
        return_value=SimpleNamespace(configuration=SimpleNamespace(configuration={}))
    )
    service.stage_failure = AsyncMock(return_value=(failed_run, False))

    settings = Settings()
    monkeypatch.setattr(job_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(job_runtime, "Database", lambda _settings: database)
    monkeypatch.setattr(job_runtime, "build_job_service", lambda **_kwargs: service)
    monkeypatch.setattr(job_runtime, "create_job_queue", lambda _settings: MagicMock())
    monkeypatch.setattr(job_runtime.JobConfiguration, "model_validate", lambda _value: MagicMock())
    monkeypatch.setattr(
        job_runtime,
        "apply_job_configuration",
        lambda _settings, _snapshot: settings,
    )
    logged = MagicMock()
    monkeypatch.setattr(job_runtime.logger, "exception", logged)

    async def provider_failure(*_args: object, **_kwargs: object) -> None:
        raise ProviderAuthenticationError(
            "Google Vision OCR rejected the configured credentials.",
            provider_name="google_vision",
        )

    await job_runtime.run_durable_job(
        project_id=project_id,
        job_id=run_id,
        expected_type=JobType.DOCUMENT_PROCESS,
        operation=provider_failure,
    )

    failure = service.stage_failure.await_args.kwargs["failure"]
    session.expunge.assert_called_once_with(run)
    assert service.stage_failure.await_args.args[0] == run_id
    assert failure.code == "provider_authentication_error"
    assert failure.message == "Google Vision OCR rejected the configured credentials."
    assert failure.details == {"provider": "google_vision"}
    session.commit.assert_awaited_once()
    logged.assert_called_once_with(
        "durable_job_failed",
        project_id=str(project_id),
        job_id=str(run_id),
        failure_code="provider_authentication_error",
        retry_scheduled=False,
    )


async def test_operation_job_updates_are_deferred_to_terminal_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        project_id=project_id,
        document_id=None,
        job_type=JobType.STORAGE_RECONCILE,
        payload={"request": "original"},
        result=None,
    )
    session = AsyncMock()
    session.expunge = MagicMock()

    @asynccontextmanager
    async def session_scope():
        yield session

    database = MagicMock()
    database.session_factory.side_effect = session_scope
    database.dispose = AsyncMock()

    service = MagicMock()
    service.acquire = AsyncMock(return_value=run)
    service.get_detail = AsyncMock(
        return_value=SimpleNamespace(configuration=SimpleNamespace(configuration={}))
    )
    service.stage_success = AsyncMock(return_value=None)

    settings = Settings()
    monkeypatch.setattr(job_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(job_runtime, "Database", lambda _settings: database)
    monkeypatch.setattr(job_runtime, "build_job_service", lambda **_kwargs: service)
    monkeypatch.setattr(job_runtime, "create_job_queue", lambda _settings: MagicMock())
    monkeypatch.setattr(job_runtime.JobConfiguration, "model_validate", lambda _value: MagicMock())
    monkeypatch.setattr(
        job_runtime,
        "apply_job_configuration",
        lambda _settings, _snapshot: settings,
    )

    async def operation(*_args: object, **_kwargs: object) -> None:
        session.expunge.assert_called_once_with(run)
        run.payload = {**run.payload, "build_id": "build-1"}
        run.result = {"consistent": True}
        return None

    await job_runtime.run_durable_job(
        project_id=project_id,
        job_id=run_id,
        expected_type=JobType.STORAGE_RECONCILE,
        operation=operation,
    )

    service.stage_success.assert_awaited_once_with(
        run_id,
        worker_id=service.stage_success.await_args.kwargs["worker_id"],
        child=None,
        payload={"request": "original", "build_id": "build-1"},
        result={"consistent": True},
    )
