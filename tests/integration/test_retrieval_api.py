"""Integration tests for retrieval API."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.composition.retrieval import build_indexing_service
from app.core.config import get_settings
from app.models.chunk_embedding import ChunkEmbedding
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.models.keyword_term_stats import KeywordTermStats
from app.modules.retrieval.repositories.chunk_embedding_repository import (
    ChunkEmbeddingRepository,
)
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.contracts import JobDefinition
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from tests.conftest import CapturingJobQueue
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_index_jobs,
    run_captured_lifecycle_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_UNIQUE_PHRASE = "retrieval-e2e-zephyr-quantum-unique-phrase-42"


async def test_pgvector_extension_column_and_hnsw_index(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
) -> None:
    assert db_client is not None
    extension_version = await integration_connection.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    column_type = await integration_connection.scalar(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute AS a
            JOIN pg_class AS c ON c.oid = a.attrelid
            WHERE c.relname = 'chunk_embeddings'
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
    )
    index_rows = await integration_connection.execute(
        text(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE indexname IN (
              'ix_chunk_embeddings_embedding_hnsw_cosine',
              'ix_chunk_embeddings_semantic_scope',
              'ix_document_chunks_metadata_gin'
            )
            """
        )
    )
    index_definitions = {row.indexname: row.indexdef for row in index_rows}

    assert extension_version
    assert column_type == "vector(384)"
    hnsw_definition = index_definitions["ix_chunk_embeddings_embedding_hnsw_cosine"]
    assert "USING hnsw" in hnsw_definition
    assert "vector_cosine_ops" in hnsw_definition
    assert "ix_chunk_embeddings_semantic_scope" in index_definitions
    assert "USING gin" in index_definitions["ix_document_chunks_metadata_gin"]


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
    assert embed.status_code == 202
    assert embed.json()["data"]["status"] == "embedding"
    assert len(captured_jobs) == 1
    assert captured_jobs[0].name == "document.embed"

    await run_captured_embed_jobs(integration_connection, captured_jobs)
    embedded = await db_client.get(f"/api/v1/projects/{project_id}/documents/{document_id}")
    assert embedded.json()["data"]["status"] == "ready"

    not_ready_search = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE},
    )
    assert not_ready_search.status_code == 200
    assert len(not_ready_search.json()["data"]["results"]) >= 1

    index = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/index",
    )
    assert index.status_code == 202
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

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        await session.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == uuid.UUID(document_id))
            .values(chunk_metadata={"source": "handbook"})
        )
        await session.commit()

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

    matching = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "metadata_filter": {"source": "handbook"}},
    )
    assert matching.status_code == 200
    assert matching.json()["data"]["results"]

    # A filterable key ("source") with a non-matching value excludes every hit.
    filtered = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "metadata_filter": {"source": "no-such-source"}},
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["results"] == []


async def test_search_document_and_embedding_version_filters(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    first_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"First {_UNIQUE_PHRASE}.".encode(),
    )
    second_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"Second {_UNIQUE_PHRASE}.".encode(),
    )
    for document_id in (first_id, second_id):
        await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
        await run_captured_embed_jobs(integration_connection, captured_jobs)
        await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
        await run_captured_index_jobs(integration_connection, captured_jobs)

    restricted = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "document_id": first_id},
    )
    assert restricted.status_code == 200
    assert restricted.json()["data"]["results"]
    assert {hit["document_id"] for hit in restricted.json()["data"]["results"]} == {first_id}

    await integration_connection.execute(
        text(
            """
            UPDATE chunk_embeddings SET embedding_set_version = 2
            WHERE project_id = :project_id
            """
        ),
        {"project_id": uuid.UUID(project_id)},
    )
    stale = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE, "strategy": "semantic"},
    )
    assert stale.status_code == 200
    assert stale.json()["data"]["results"] == []


async def test_auto_embed_index_chain(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    """With auto flags on, embedding publishes one complete vector+keyword build."""
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

    def _service(session: AsyncSession) -> IndexingService:
        return build_indexing_service(
            session=session,
            project_id=uuid.UUID(project_id),
            settings=auto_settings,
            job_queue=CapturingJobQueue(captured_jobs),
        )

    document_uuid = uuid.UUID(document_id)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        await _service(session).enqueue_embed_if_enabled(document_uuid)
    assert [job.name for job in captured_jobs] == ["document.embed"]
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    assert captured_jobs == []

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


async def test_pgvector_cosine_ranking_and_score_threshold(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _create_project(db_client)
    document_ids = []
    for label in ("near", "far"):
        document_id = await _upload_chunked(
            db_client,
            integration_connection,
            captured_jobs,
            project_id,
            content=f"{label} {_UNIQUE_PHRASE}".encode(),
        )
        document_ids.append(document_id)
        await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
        await run_captured_embed_jobs(integration_connection, captured_jobs)
        await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
        await run_captured_index_jobs(integration_connection, captured_jobs)

    settings = get_settings()
    embedder = create_embedding_provider(settings)
    query_vector = (await embedder.embed_texts([_UNIQUE_PHRASE])).vectors[0]
    project_uuid = uuid.UUID(project_id)
    near_document_id, far_document_id = map(uuid.UUID, document_ids)

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        rows = list(
            (
                await session.execute(
                    select(ChunkEmbedding).where(
                        ChunkEmbedding.project_id == project_uuid,
                        ChunkEmbedding.document_id.in_([near_document_id, far_document_id]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.embedding = (
                query_vector
                if row.document_id == near_document_id
                else [-value for value in query_vector]
            )
        await session.flush()

        repository = ChunkEmbeddingRepository(session, project_uuid)
        hits = await repository.search_cosine(
            query_vector=query_vector,
            top_k=10,
            embedding_set_version=1,
            provider=embedder.provider_name,
            model=embedder.model_name,
            score_threshold=0.5,
        )

    near_chunk_ids = {row.chunk_id for row in rows if row.document_id == near_document_id}
    assert hits
    assert {hit.chunk_id for hit in hits} == near_chunk_ids
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)


async def test_search_hybrid_prefers_exact_keyword_match(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    """Hybrid strategy should rank exact keyword token above semantic-only neighbors."""
    project_id = await _create_project(db_client)
    rare_token = "kwzraretoken987654"
    filler = "semantic filler content about general workplace topics " * 5
    document_id = await _upload_chunked(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        content=f"{filler}\n\nSection {rare_token} details.".encode(),
    )

    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    search = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": rare_token, "top_k": 5, "strategy": "hybrid", "rerank": False},
    )
    assert search.status_code == 200
    results = search.json()["data"]["results"]
    assert len(results) >= 1
    assert rare_token in results[0]["content"]


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

    project_uuid = uuid.UUID(project_id)
    document_uuid = uuid.UUID(document_id)

    deleted = await db_client.delete(
        f"/api/v1/projects/{project_id}/documents/{document_id}",
    )
    assert deleted.status_code == 202
    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)

    embedding_count = await integration_connection.scalar(
        select(func.count())
        .select_from(ChunkEmbedding)
        .where(
            ChunkEmbedding.project_id == project_uuid,
            ChunkEmbedding.document_id == document_uuid,
        )
    )
    keyword_count = await integration_connection.scalar(
        select(func.count())
        .select_from(ChunkKeywordIndex)
        .where(
            ChunkKeywordIndex.project_id == project_uuid,
            ChunkKeywordIndex.document_id == document_uuid,
        )
    )
    term_count = await integration_connection.scalar(
        select(func.count())
        .select_from(KeywordTermStats)
        .where(
            KeywordTermStats.project_id == project_uuid,
        )
    )
    assert embedding_count and embedding_count > 0
    assert keyword_count and keyword_count > 0
    assert term_count and term_count > 0

    search = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _UNIQUE_PHRASE},
    )
    assert search.status_code == 200
    assert search.json()["data"]["results"] == []


async def test_reembedding_and_reindexing_are_idempotent(
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
        content=f"Idempotent recovery {_UNIQUE_PHRASE}.".encode(),
    )
    document_path = f"/api/v1/projects/{project_id}/documents/{document_id}"

    await db_client.post(f"{document_path}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"{document_path}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    async def _counts() -> tuple[int, int, list[tuple[str, int]]]:
        project_uuid = uuid.UUID(project_id)
        document_uuid = uuid.UUID(document_id)
        embedding_count = int(
            await integration_connection.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(
                    ChunkEmbedding.project_id == project_uuid,
                    ChunkEmbedding.document_id == document_uuid,
                )
            )
            or 0
        )
        keyword_count = int(
            await integration_connection.scalar(
                select(func.count())
                .select_from(ChunkKeywordIndex)
                .where(
                    ChunkKeywordIndex.project_id == project_uuid,
                    ChunkKeywordIndex.document_id == document_uuid,
                )
            )
            or 0
        )
        frequencies = list(
            (
                await integration_connection.execute(
                    select(
                        KeywordTermStats.term,
                        KeywordTermStats.document_frequency,
                    )
                    .where(KeywordTermStats.project_id == project_uuid)
                    .order_by(KeywordTermStats.term)
                )
            ).all()
        )
        return embedding_count, keyword_count, frequencies

    initial = await _counts()
    await db_client.post(f"{document_path}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    await db_client.post(f"{document_path}/index")
    await run_captured_index_jobs(integration_connection, captured_jobs)

    assert await _counts() == initial
    ready = await db_client.get(document_path)
    assert ready.json()["data"]["status"] == "ready"
