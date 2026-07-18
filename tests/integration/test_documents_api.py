"""Integration tests for document upload API."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.platform.jobs.contracts import JobDefinition
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_lifecycle_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _create_project(client: AsyncClient, name: str | None = None) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": name or f"Docs Project {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def _upload_and_process(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
    project_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str = "text/plain",
) -> dict[str, object]:
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, content, content_type)},
    )
    assert upload.status_code == 201
    assert len(captured_jobs) == 1
    document_id = upload.json()["data"]["id"]
    await run_captured_document_jobs(integration_connection, captured_jobs)
    fetched = await db_client.get(
        f"/api/v1/projects/{project_id}/documents/{document_id}",
    )
    assert fetched.status_code == 200
    return fetched.json()["data"]


async def test_upload_list_get_delete_document(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    content = b"Phase 1 knowledge upload"
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="sample.txt",
        content=content,
    )
    assert body["status"] == "chunked"
    assert body["version"] == 1
    assert body["size_bytes"] == len(content)
    assert body["page_count"] == 1
    document_id = body["id"]

    settings = get_settings()
    storage_path = Path(settings.storage.local_root) / str(body["storage_key"])
    assert storage_path.is_file()
    parsed_path = Path(settings.storage.local_root) / str(body["parsed_text_storage_key"])
    assert parsed_path.is_file()
    parsed_json_path = parsed_path.with_suffix(".json")
    assert parsed_json_path.is_file()

    listed = await db_client.get(f"/api/v1/projects/{project_id}/documents")
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["data"]["items"]}
    assert document_id in ids

    fetched = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["filename"] == "sample.txt"
    assert fetched.json()["data"]["status"] == "chunked"

    deleted = await db_client.delete(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert deleted.status_code == 202
    assert deleted.json()["data"]["status"] == "deleting"
    assert deleted.json()["data"]["deleted_at"] is None
    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)
    assert storage_path.is_file()
    assert parsed_path.is_file()
    assert parsed_json_path.is_file()

    list_after = await db_client.get(f"/api/v1/projects/{project_id}/documents")
    assert document_id not in {item["id"] for item in list_after.json()["data"]["items"]}


async def test_purge_removes_document_and_every_storage_artifact(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="purge-me.txt",
        content=b"This corpus artifact must be irreversibly purged.",
    )
    document_id = body["id"]
    settings = get_settings()
    raw_path = Path(settings.storage.local_root) / str(body["storage_key"])
    parsed_path = Path(settings.storage.local_root) / str(body["parsed_text_storage_key"])
    parsed_json_path = parsed_path.with_suffix(".json")
    assert raw_path.is_file()
    assert parsed_path.is_file()
    assert parsed_json_path.is_file()

    purging = await db_client.delete(
        f"/api/v1/projects/{project_id}/documents/{document_id}/purge",
    )
    assert purging.status_code == 202
    data = purging.json()["data"]
    assert data["status"] == "purging"
    job_id = data["job_id"]

    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)

    fetched = await db_client.get(
        f"/api/v1/projects/{project_id}/documents/{document_id}",
    )
    assert fetched.status_code == 404
    assert not raw_path.exists()
    assert not parsed_path.exists()
    assert not parsed_json_path.exists()

    job = await db_client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")
    assert job.status_code == 200
    job_data = job.json()["data"]
    assert job_data["state"] == "succeeded"
    assert job_data["document_id"] is None
    assert job_data["result"]["document_id"] == document_id
    assert job_data["result"]["mode"] == "purge"


async def test_upload_duplicate_content_returns_conflict(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    files = {"file": ("a.txt", b"duplicate-me", "text/plain")}

    first = await db_client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert first.status_code == 201
    await run_captured_document_jobs(integration_connection, captured_jobs)

    second = await db_client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "document_content_duplicate"


async def test_document_isolated_by_project(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_a = await _create_project(db_client)
    project_b = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_a,
        filename="iso.txt",
        content=b"isolated",
    )
    document_id = body["id"]

    cross = await db_client.get(f"/api/v1/projects/{project_b}/documents/{document_id}")
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "document_not_found"


async def test_upload_to_missing_project_returns_not_found(db_client: AsyncClient) -> None:
    missing = uuid.uuid4()
    response = await db_client.post(
        f"/api/v1/projects/{missing}/documents",
        files={"file": ("x.txt", b"x", "text/plain")},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


async def test_upload_returns_queued_before_worker(
    db_client: AsyncClient,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("queued.txt", b"queued", "text/plain")},
    )
    assert upload.status_code == 201
    assert upload.json()["data"]["status"] == "queued"
    assert len(captured_jobs) == 1


async def test_parse_text_document_via_workflow(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="notes.md",
        content=b"# Title\n\nBody text",
        content_type="text/markdown",
    )
    assert body["status"] == "chunked"
    assert body["parser_name"] == "plain_text"
    assert body["parsed_text_storage_key"] is not None


async def test_unsupported_file_fails_before_job_creation(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    del integration_connection
    response = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("data.bin", b"\x00\x01\x02", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "document_type_unsupported"
    assert captured_jobs == []


async def test_reprocess_deleted_document_returns_conflict(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="reprocess.txt",
        content=b"reprocess me",
    )
    document_id = body["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}/documents/{document_id}")

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/reprocess",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_lifecycle_pending"


async def test_reprocess_bumps_version_and_rechunks(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="versioned.txt",
        content=b"reprocess version bump",
    )
    document_id = body["id"]
    assert body["version"] == 1

    reprocess = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/reprocess",
    )
    assert reprocess.status_code == 200
    assert reprocess.json()["data"]["status"] == "queued"
    assert reprocess.json()["data"]["version"] == 2
    assert len(captured_jobs) == 1

    await run_captured_document_jobs(integration_connection, captured_jobs)
    fetched = await db_client.get(
        f"/api/v1/projects/{project_id}/documents/{document_id}",
    )
    data = fetched.json()["data"]
    assert data["status"] == "chunked"
    assert data["version"] == 2
    assert "v2" in data["parsed_text_storage_key"]

    chunks = await db_client.get(
        f"/api/v1/projects/{project_id}/documents/{document_id}/chunks",
    )
    assert chunks.json()["data"]["total"] >= 1


async def test_upload_exceeding_size_limit_returns_413(
    db_client: AsyncClient,
    captured_jobs: list[JobDefinition],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _create_project(db_client)
    monkeypatch.setenv("APE_KNOWLEDGE__MAX_UPLOAD_BYTES", "16")
    get_settings.cache_clear()
    try:
        response = await db_client.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"file": ("big.txt", b"x" * 64, "text/plain")},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "document_too_large"
        assert captured_jobs == []
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


async def test_list_document_chunks(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    content = b"chunking test " * 80
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        filename="chunks.txt",
        content=content,
    )
    document_id = body["id"]

    response = await db_client.get(
        f"/api/v1/projects/{project_id}/documents/{document_id}/chunks",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    assert data["items"][0]["chunk_index"] == 0
    assert data["items"][0]["content"]
    assert data["items"][0]["token_count"] is not None


async def test_chunks_isolated_by_project(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_a = await _create_project(db_client)
    project_b = await _create_project(db_client)
    body = await _upload_and_process(
        db_client,
        integration_connection,
        captured_jobs,
        project_a,
        filename="iso-chunks.txt",
        content=b"isolated chunks",
    )
    document_id = body["id"]

    cross = await db_client.get(
        f"/api/v1/projects/{project_b}/documents/{document_id}/chunks",
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "document_not_found"
