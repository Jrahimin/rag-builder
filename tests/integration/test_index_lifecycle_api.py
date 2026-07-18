"""Integration coverage for isolated builds, activation, rollback, and reconciliation."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.platform.jobs.contracts import JobDefinition
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_lifecycle_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _ready_document(
    client: AsyncClient,
    connection: AsyncConnection,
    jobs: list[JobDefinition],
) -> tuple[str, str]:
    project = await client.post(
        "/api/v1/projects", json={"name": f"Lifecycle {uuid.uuid4().hex[:8]}"}
    )
    project_id = project.json()["data"]["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("lifecycle.txt", b"safe active corpus zephyr", "text/plain")},
    )
    document_id = upload.json()["data"]["id"]
    await run_captured_document_jobs(connection, jobs)
    await client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(connection, jobs)
    return project_id, document_id


async def test_bad_build_isolated_then_activation_and_rollback_are_atomic(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id, _ = await _ready_document(db_client, integration_connection, captured_jobs)
    builds_path = f"/api/v1/projects/{project_id}/index-builds"
    initial = (await db_client.get(builds_path)).json()["data"]
    original_active = initial["active_build_id"]
    assert original_active

    staged = await db_client.post(f"{builds_path}/reindex")
    assert staged.status_code == 202
    new_build = staged.json()["data"]["build_id"]

    rejected = await db_client.post(f"{builds_path}/{new_build}/activate")
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "index_build_not_activatable"
    assert (await db_client.get(builds_path)).json()["data"]["active_build_id"] == original_active

    search_during_build = await db_client.post(
        f"/api/v1/projects/{project_id}/search", json={"query": "zephyr"}
    )
    assert len(search_during_build.json()["data"]["results"]) == 1

    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)
    validated = (await db_client.get(builds_path)).json()["data"]
    assert validated["active_build_id"] == original_active
    assert (
        next(item for item in validated["items"] if item["id"] == new_build)["state"] == "validated"
    )

    activated = await db_client.post(f"{builds_path}/{new_build}/activate")
    assert activated.status_code == 200
    after_activation = (await db_client.get(builds_path)).json()["data"]
    assert after_activation["active_build_id"] == new_build
    assert after_activation["previous_build_id"] == original_active

    rolled_back = await db_client.post(f"{builds_path}/rollback")
    assert rolled_back.status_code == 200
    after_rollback = (await db_client.get(builds_path)).json()["data"]
    assert after_rollback["active_build_id"] == original_active
    assert after_rollback["previous_build_id"] == new_build


async def test_storage_reconciliation_persists_structured_result(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id, _ = await _ready_document(db_client, integration_connection, captured_jobs)
    staged = await db_client.post(f"/api/v1/projects/{project_id}/index-builds/reconcile-storage")
    assert staged.status_code == 202
    job_id = staged.json()["data"]["job_id"]
    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)

    job = await db_client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")
    assert job.status_code == 200
    result = job.json()["data"]["result"]
    assert result["consistent"] is True
    assert result["missing"] == []
    assert result["orphan"] == []
