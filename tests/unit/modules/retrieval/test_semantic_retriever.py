"""Unit tests for SemanticRetriever hydration and search filters."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.platform.providers.contracts.vector_store import VectorPoint
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.memory_vector_store import MemoryVectorStoreProvider

pytestmark = pytest.mark.unit

_DIMENSIONS = 16


def _embedder() -> HashEmbeddingProvider:
    return HashEmbeddingProvider(model="m", dimensions=_DIMENSIONS, provider_version="1")


def _chunk(project_id: uuid.UUID, document_id: uuid.UUID) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        project_id=project_id,
        document_id=document_id,
        chunk_index=0,
        content="chunk content",
        page_number=1,
        char_start=0,
        char_end=13,
        token_count=2,
        chunk_metadata={"source": "handbook"},
    )


def _document(project_id: uuid.UUID) -> Document:
    return Document(
        id=uuid.uuid4(),
        project_id=project_id,
        filename="doc.txt",
        content_type="text/plain",
        size_bytes=10,
        storage_key="k",
        content_sha256="abc",
        status=DocumentStatus.READY,
        version=1,
    )


async def _seed_point(
    store: MemoryVectorStoreProvider,
    *,
    point_id: str,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    embedding_set_version: int,
    text: str = "chunk content",
) -> None:
    vectors = (await _embedder().embed_texts([text])).vectors
    await store.upsert_points(
        [
            VectorPoint(
                point_id=point_id,
                vector=vectors[0],
                payload={
                    "project_id": str(project_id),
                    "document_id": str(document_id),
                    "embedding_set_version": embedding_set_version,
                    "source": "handbook",
                },
            )
        ]
    )


def _retriever(
    project_id: uuid.UUID,
    store: MemoryVectorStoreProvider,
    *,
    embedding_set_version: int = 1,
    filterable_metadata_keys: list[str] | None = None,
) -> SemanticRetriever:
    retriever = SemanticRetriever(
        session=AsyncMock(),
        project_id=project_id,
        embedder=_embedder(),
        vector_store=store,
        default_top_k=10,
        score_threshold=None,
        filterable_metadata_keys=(
            filterable_metadata_keys if filterable_metadata_keys is not None else ["source"]
        ),
        embedding_set_version=embedding_set_version,
    )
    return retriever


def _wire_repositories(
    retriever: SemanticRetriever,
    chunks: list[DocumentChunk],
    documents: list[Document],
) -> None:
    chunk_repository = MagicMock()
    chunk_repository.map_by_ids = AsyncMock(return_value={chunk.id: chunk for chunk in chunks})
    document_repository = MagicMock()
    document_repository.map_by_ids = AsyncMock(
        return_value={document.id: document for document in documents}
    )
    retriever._chunk_repository = chunk_repository
    retriever._document_repository = document_repository


async def test_search_hydrates_chunks_and_documents() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document = _document(project_id)
    chunk = _chunk(project_id, document.id)
    await _seed_point(
        store,
        point_id=str(chunk.id),
        project_id=project_id,
        document_id=document.id,
        embedding_set_version=1,
    )

    retriever = _retriever(project_id, store)
    _wire_repositories(retriever, [chunk], [document])

    results = await retriever.search(query="chunk content")

    assert len(results) == 1
    assert results[0].chunk_id == chunk.id
    assert results[0].document_id == document.id
    assert results[0].content == "chunk content"
    assert results[0].filename == "doc.txt"


async def test_search_filters_by_embedding_set_version() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document = _document(project_id)
    current = _chunk(project_id, document.id)
    stale = _chunk(project_id, document.id)
    await _seed_point(
        store,
        point_id=str(current.id),
        project_id=project_id,
        document_id=document.id,
        embedding_set_version=2,
    )
    await _seed_point(
        store,
        point_id=str(stale.id),
        project_id=project_id,
        document_id=document.id,
        embedding_set_version=1,
    )

    retriever = _retriever(project_id, store, embedding_set_version=2)
    _wire_repositories(retriever, [current, stale], [document])

    results = await retriever.search(query="chunk content")

    assert [result.chunk_id for result in results] == [current.id]


async def test_search_drops_hits_without_chunk_rows() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document_id = uuid.uuid4()
    await _seed_point(
        store,
        point_id=str(uuid.uuid4()),
        project_id=project_id,
        document_id=document_id,
        embedding_set_version=1,
    )

    retriever = _retriever(project_id, store)
    _wire_repositories(retriever, [], [])

    results = await retriever.search(query="chunk content")

    assert results == []


async def test_search_ignores_unfilterable_metadata_keys() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document = _document(project_id)
    chunk = _chunk(project_id, document.id)
    await _seed_point(
        store,
        point_id=str(chunk.id),
        project_id=project_id,
        document_id=document.id,
        embedding_set_version=1,
    )

    retriever = _retriever(project_id, store, filterable_metadata_keys=["source"])
    _wire_repositories(retriever, [chunk], [document])

    # "secret" is not filterable, so it must be stripped rather than excluding hits.
    results = await retriever.search(
        query="chunk content",
        metadata_filter={"secret": "nope"},
    )
    assert len(results) == 1

    # "source" is filterable, so a non-matching value excludes the hit.
    results = await retriever.search(
        query="chunk content",
        metadata_filter={"source": "other"},
    )
    assert results == []
