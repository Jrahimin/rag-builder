"""Operator metrics, worker degradation, and sanitization tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailableError
from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.modules.operations.repositories.operator_repository import OperatorRepository
from app.modules.operations.schemas.operator import UsageBucket, UsageWorkload
from app.modules.operations.services.operator_service import OperatorService
from app.platform.jobs.worker_registry import WorkerHeartbeat
from app.platform.system.schemas import PreflightStatus


def _service(repository: MagicMock, *, registry: MagicMock | None = None) -> OperatorService:
    settings = Settings()
    return OperatorService(
        settings=settings,
        repository=repository,
        health=MagicMock(),
        worker_registry=registry or MagicMock(list=AsyncMock(return_value=[])),
        preflight=PreflightStatus(
            status="ready",
            profile="development",
            checked_at=datetime.now(UTC),
            checks=[],
        ),
    )


async def test_metrics_composes_job_latency_usage_and_corpus() -> None:
    repository = MagicMock(spec=OperatorRepository)
    repository.job_state_counts = AsyncMock(return_value={"queued": 2, "failed": 1})
    repository.job_retry_attempts = AsyncMock(return_value=3)
    repository.failures_since = AsyncMock(return_value=1)
    repository.oldest_job_queue_time = AsyncMock(return_value=None)
    repository.outbox_metrics = AsyncMock(return_value=(2, None, 4))
    repository.job_latency = AsyncMock(return_value=[("document.process", 2, 12.5, 20.0)])
    repository.chat_latency = AsyncMock(side_effect=[(3, 4.0, 6.0), (3, 8.0, 10.0)])
    repository.provider_generation_latency = AsyncMock(return_value=[("echo", 3, 8.0, 10.0)])
    repository.token_usage = AsyncMock(return_value=(100, 50))
    repository.corpus_counts = AsyncMock(return_value=(1, 2, 10, 4096))

    result = await _service(repository).metrics()

    assert result.jobs.total == 3
    assert result.jobs.pending_dispatches == 2
    assert result.token_usage.total_tokens == 150
    assert result.corpus.storage_bytes == 4096
    assert result.job_latency[0].name == "document.process"


async def test_worker_registry_failure_returns_actionable_degraded_state() -> None:
    registry = MagicMock(list=AsyncMock(side_effect=ConnectionError("redis down")))
    result = await _service(MagicMock(spec=OperatorRepository), registry=registry).workers()
    assert result.available is False
    assert result.active_count == 0
    assert "Redis" in (result.detail or "")


async def test_worker_heartbeat_is_reported_active() -> None:
    now = datetime.now(UTC).isoformat()
    registry = MagicMock(
        list=AsyncMock(
            return_value=[
                WorkerHeartbeat(
                    worker_id="worker-1",
                    hostname="host",
                    process_id=10,
                    started_at=now,
                    heartbeat_at=now,
                    version="1",
                )
            ]
        )
    )
    result = await _service(MagicMock(spec=OperatorRepository), registry=registry).workers()
    assert result.active_count == 1
    assert result.workers[0].state == "active"


async def test_active_configuration_never_returns_secret_values() -> None:
    project_id = uuid.uuid4()
    repository = MagicMock(spec=OperatorRepository)
    repository.recent_configuration_snapshots = AsyncMock(
        return_value=[
            JobConfigurationSnapshot(
                id=uuid.uuid4(),
                project_id=project_id,
                schema_version=1,
                configuration_hash="a" * 64,
                configuration={},
                created_at=datetime.now(UTC),
            )
        ]
    )
    result = await _service(repository).active_configuration()
    payload = result.model_dump_json()
    assert "api_key" not in payload
    assert "password" not in payload
    assert result.llm.credential_configured is None


async def test_metrics_database_failure_is_sanitized() -> None:
    repository = MagicMock(spec=OperatorRepository)
    repository.job_state_counts = AsyncMock(
        side_effect=OperationalError("select", {}, ConnectionError("secret database detail"))
    )
    try:
        await _service(repository).metrics()
    except ServiceUnavailableError as exc:
        assert exc.code == "operator_data_unavailable"
        assert "secret" not in exc.message
    else:
        raise AssertionError("Expected a sanitized service-unavailable error")


async def test_usage_preserves_dimensions_and_unknown_provider_tokens() -> None:
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    started = datetime(2026, 8, 1, tzinfo=UTC)
    ended = datetime(2026, 8, 2, tzinfo=UTC)
    repository = MagicMock(spec=OperatorRepository)
    row = {
        "bucket_start": started,
        "organization_id": organization_id,
        "organization_name": "Example Org",
        "project_id": project_id,
        "project_name": "Example Project",
        "provider": "gemini",
        "model": "gemini-test",
        "workload": "chat",
        "request_count": 2,
        "error_count": 0,
        "records_with_token_usage": 1,
        "input_tokens": None,
        "output_tokens": None,
        "retrieval_samples": 2,
        "retrieval_average_ms": 12.5,
        "retrieval_maximum_ms": 15,
        "provider_samples": 2,
        "provider_average_ms": 20,
        "provider_maximum_ms": 25,
        "total_samples": 2,
        "total_average_ms": 32.5,
        "total_maximum_ms": 40,
    }
    repository.usage_aggregates = AsyncMock(return_value=([row], row))

    result = await _service(repository).usage(
        start_at=started,
        end_at=ended,
        bucket=UsageBucket.DAY,
        organization_id=organization_id,
        project_id=project_id,
        provider="gemini",
        model="gemini-test",
        workload=UsageWorkload.CHAT,
    )

    assert result.items[0].organization_id == organization_id
    assert result.items[0].project_id == project_id
    assert result.items[0].workload is UsageWorkload.CHAT
    assert result.items[0].input_tokens is None
    assert result.items[0].total_tokens is None
    assert result.items[0].records_with_token_usage == 1
    assert result.items[0].provider_latency.average_ms == 20.0
    repository.usage_aggregates.assert_awaited_once_with(
        start_at=started,
        end_at=ended,
        bucket=UsageBucket.DAY,
        organization_id=organization_id,
        project_id=project_id,
        provider="gemini",
        model="gemini-test",
        workload=UsageWorkload.CHAT,
    )

    repository.usage_aggregates.reset_mock()
    await _service(repository).usage(
        start_at=started.replace(tzinfo=None),
        end_at=ended.replace(tzinfo=None),
        bucket=UsageBucket.DAY,
        organization_id=organization_id,
        project_id=project_id,
        provider="gemini",
        model="gemini-test",
        workload=UsageWorkload.CHAT,
    )
    normalized = repository.usage_aggregates.await_args.kwargs
    assert normalized["start_at"] == started
    assert normalized["end_at"] == ended
