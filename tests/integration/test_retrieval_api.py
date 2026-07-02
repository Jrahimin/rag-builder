"""Integration tests for retrieval API."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.contracts import JobDefinition
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.vector_store_factory import create_vector_store_provider
from tests.conftest import CapturingJobQueue
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_index_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_UNIQUE_PHRASE = "retrieval-e2e-zephyr-quantum-unique-phrase-42"


async def _create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"Retrieval Project {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def _upload_chunked(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
    project_id: str,
    *,
    content: bytes,
) -> str:
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("retrieval.txt", content, "text/plain")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["data"]["id"]
    await run_captured_document_jobs(integration_connection, captured_jobs)
    fetched = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert fetched.json()["data"]["status"] == "chunked"
    return document_id


async def test_embed_and_index_document(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Intro paragraph.\n\n{_UNIQUE_PHRASE} appears here.".encode(),
    )

    embed = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/embed",
    )
    assert embed.status_code == 200
    assert embed.json()["data"]["status"] == "embedding"
    assert len(captured_jobs) == 1
    assert captured_jobs[0].name == "document.embed"

    await run_captured_embed_jobs(integration_connection, captured_jobs)
    embedded = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert embedded.json()["data"]["status"] == "embedded"

    index = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/index",
    )
    assert index.status_code == 200
    assert index.json()["data"]["status"] == "indexing"
    assert len(captured_jobs) == 1
    assert captured_jobs[0].name == "document.index"

    await run_captured_index_jobs(integration_connection, captured_jobs)
    ready = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert ready.json()["data"]["status"] == "ready"


async def test_search_returns_matching_chunk(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Background context.\n\n{_UNIQUE_PHRASE} for semantic search.".encode(),
    )

    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    search = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "top_k": 5},
    )
    assert search.status_code == 200
    results = search.json()["data"]["results"]
    assert len(results) >= 1
    assert any(_UNIQUE_PHRASE in hit["content"] for hit in results)
    assert results[0]["document_id"] == document_id
    assert "filename" in results[0]


async def test_search_metadata_filter(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Filtered content.\n\n{_UNIQUE_PHRASE} with metadata.".encode(),
    )

    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    # A non-filterable key is stripped from the filter, so results still come back.
    unfiltered = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "metadata_filter": {"not_allowed": "x"}},
    )
    assert unfiltered.status_code == 200
    assert len(unfiltered.json()["data"]["results"]) >= 1

    # A filterable key ("source") with a non-matching value excludes every hit.
    filtered = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "metadata_filter": {"source": "no-such-source"}},
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["results"] == []


async def test_auto_embed_index_chain(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    """With auto flags on, embed completion enqueues indexing automatically."""
    project_id = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Auto chain.\n\n{_UNIQUE_PHRASE} end to end.".encode(),
    )

    settings = get_settings()
    auto_settings = settings.model_copy(
        update={
            "retrieval": settings.retrieval.model_copy(
                update={"auto_embed": True, "auto_index": True}
            )
        }
    )

    async def _noop_ensure_project() -> None:
        return None

    def _service(session: AsyncSession) -> IndexingService:
        return IndexingService.from_settings(
            session=session,
            project_id=uuid.UUID(project_id),
            settings=auto_settings,
            ensure_project=_noop_ensure_project,
            job_queue=CapturingJobQueue(captured_jobs),
            embedder=create_embedding_provider(settings),
            vector_store=create_vector_store_provider(settings),
        )

    document_uuid = uuid.UUID(document_id)
    async with AsyncSession(bind=integration_connection, expire_on_commit=False) as session:
        await _service(session).enqueue_embed_if_enabled(document_uuid)
    assert [job.name for job in captured_jobs] == ["document.embed"]
    captured_jobs.clear()

    async with AsyncSession(bind=integration_connection, expire_on_commit=False) as session:
        await _service(session).run_embed(document_uuid)
    assert [job.name for job in captured_jobs] == ["document.index"]
    captured_jobs.clear()

    async with AsyncSession(bind=integration_connection, expire_on_commit=False) as session:
        await _service(session).run_index(document_uuid)

    ready = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert ready.json()["data"]["status"] == "ready"


async def test_search_isolated_by_project(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_a = await _create_project(db_client)
    project_b = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_a,
        content=f"Secret {_UNIQUE_PHRASE} project A only.".encode(),
    )

    await db_client.post(f"/api/v1/projects/{project_a}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_a}/documents/{document_id}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    search = await db_client.post(
        f"/api/v1/projects/{project_b}/search",
        json={"query": _UNIQUE_PHRASE},
    )
    assert search.status_code == 200
    assert search.json()["data"]["results"] == []


async def test_deleted_document_not_in_search(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Delete me {_UNIQUE_PHRASE}.".encode(),
    )

    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    deleted = await db_client.delete(
        f"/api/v1/projects/{project_id}/documents/{document_id}",
    )
    assert deleted.status_code == 200

    search = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE},
    )
    assert search.status_code == 200
    assert search.json()["data"]["results"] == []
