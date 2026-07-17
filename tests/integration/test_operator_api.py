"""Integration coverage for the admin-gated operator read model."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_operator_backend_exposes_runtime_without_secrets(db_client: AsyncClient) -> None:
    project = await db_client.post(
        "/api/v1/projects",
        json={"name": f"Operator {uuid.uuid4().hex[:8]}"},
    )
    project_id = project.json()["data"]["id"]
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("operator.txt", b"operator runtime", "text/plain")},
    )
    assert upload.status_code == 201

    metrics = await db_client.get("/api/v1/operator/metrics")
    configuration = await db_client.get("/api/v1/operator/configuration")
    failures = await db_client.get("/api/v1/operator/failures")
    audit = await db_client.get("/api/v1/operator/audit-events")
    prometheus = await db_client.get("/metrics")

    assert metrics.status_code == 200
    assert metrics.json()["data"]["jobs"]["total"] >= 1
    assert configuration.status_code == 200
    serialized_configuration = configuration.text.lower()
    assert "api_key" not in serialized_configuration
    assert "password" not in serialized_configuration
    assert failures.status_code == 200
    assert audit.status_code == 200
    assert any(event["event_type"] == "job.submitted" for event in audit.json()["data"])
    assert prometheus.status_code == 200
    assert "ape_jobs_total" in prometheus.text


async def test_operator_dependencies_and_workers_have_actionable_shapes(
    db_client: AsyncClient,
) -> None:
    dependencies = await db_client.get("/api/v1/operator/dependencies")
    workers = await db_client.get("/api/v1/operator/workers")
    assert dependencies.status_code == 200
    assert dependencies.json()["data"]["readiness"]["dependencies"]
    assert workers.status_code == 200
    assert "active_count" in workers.json()["data"]
