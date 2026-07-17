"""Unit tests for the embedding and retrieval indexing workflows."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.chunk_embedding import ChunkEmbedding
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.workflows.embedding_workflow import EmbeddingWorkflow
from app.modules.retrieval.workflows.retrieval_indexing_workflow import (
    RetrievalIndexingWorkflow,
)
from app.modules.retrieval.workflows.stage_runner import StageFailure
from app.platform.jobs.errors import PermanentJobError
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingBatchResult
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider

pytestmark = pytest.mark.unit

_DIMENSIONS = 8


class _FailingEmbedder(BaseEmbeddingProvider):
    @property
    def provider_name(self) -> str:
        return "failing"

    @property
    def model_name(self) -> str:
        return "m"

    @property
    def dimensions(self) -> int:
        return _DIMENSIONS

    @property
    def provider_version(self) -> str:
        return "1"

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        raise ProviderError("embedding backend down", provider_name="failing")


def _embedder() -> HashEmbeddingProvider:
    return HashEmbeddingProvider(model="m", dimensions=_DIMENSIONS, provider_version="1")


def _document(project_id: uuid.UUID, status: DocumentStatus) -> Document:
    return Document(
        id=uuid.uuid4(),
        project_id=project_id,
        filename="doc.txt",
        content_type="text/plain",
        size_bytes=10,
        storage_key="k",
        content_sha256="abc",
        status=status,
        version=1,
    )


def _chunk(document: Document, index: int) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        project_id=document.project_id,
        document_id=document.id,
        chunk_index=index,
        content=f"chunk {index}",
        chunk_metadata={"source": "handbook"},
    )


def _session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _repo(**methods: object) -> MagicMock:
    repository = MagicMock()
    repository.flush = AsyncMock()
    for name, value in methods.items():
        setattr(repository, name, value)
    return repository


# --- EmbeddingWorkflow ------------------------------------------------------------


def _embedding_workflow(
    project_id: uuid.UUID,
    *,
    embedder: BaseEmbeddingProvider | None = None,
) -> EmbeddingWorkflow:
    return EmbeddingWorkflow(
        session=_session(),
        project_id=project_id,
        embedder=embedder or _embedder(),
        embedding_set_version=1,
        batch_size=2,
    )


async def test_embedding_skips_missing_document() -> None:
    workflow = _embedding_workflow(uuid.uuid4())
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=None))

    assert await workflow.run(uuid.uuid4()) is None


async def test_embedding_rejects_superseded_document_version() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.PARSING)
    workflow = _embedding_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    chunk_repository = _repo(list_by_document=AsyncMock())
    workflow._chunk_repository = chunk_repository

    with pytest.raises(PermanentJobError):
        await workflow.run(document.id, expected_document_version=2)
    chunk_repository.list_by_document.assert_not_awaited()


async def test_embedding_fails_without_chunks() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.CHUNKED)
    workflow = _embedding_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._chunk_repository = _repo(list_by_document=AsyncMock(return_value=[]))

    with pytest.raises(StageFailure, match="No chunks available"):
        await workflow.run(document.id)


async def test_embedding_happy_path_persists_vectors() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.EMBEDDING)
    chunks = [_chunk(document, 0), _chunk(document, 1), _chunk(document, 2)]
    workflow = _embedding_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._chunk_repository = _repo(list_by_document=AsyncMock(return_value=chunks))
    persisted: list[ChunkEmbedding] = []
    embedding_repository = _repo(
        delete_for_document_version=AsyncMock(),
        bulk_add=MagicMock(side_effect=persisted.extend),
    )
    workflow._embedding_repository = embedding_repository

    result = await workflow.run(document.id)

    assert result is not None
    assert result.status is DocumentStatus.EMBEDDED
    assert result.error_message is None
    assert len(persisted) == 3
    assert all(row.embedding_set_version == 1 for row in persisted)
    assert all(isinstance(row.embedding, list) for row in persisted)
    embedding_repository.delete_for_document_version.assert_awaited_once()


async def test_embedding_provider_error_propagates_for_durable_classification() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.EMBEDDING)
    workflow = _embedding_workflow(project_id, embedder=_FailingEmbedder())
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._chunk_repository = _repo(
        list_by_document=AsyncMock(return_value=[_chunk(document, 0)])
    )
    workflow._embedding_repository = _repo(delete_for_document_version=AsyncMock())

    with pytest.raises(ProviderError, match="embedding backend down"):
        await workflow.run(document.id)


# --- RetrievalIndexingWorkflow ----------------------------------------------------


def _embedding_row(document: Document, chunk: DocumentChunk) -> ChunkEmbedding:
    vector = [0.5] * _DIMENSIONS
    return ChunkEmbedding(
        id=uuid.uuid4(),
        project_id=document.project_id,
        document_id=document.id,
        chunk_id=chunk.id,
        embedding_set_version=1,
        document_version=document.version,
        provider="hash",
        model="m",
        dimensions=_DIMENSIONS,
        provider_version="1",
        input_content_hash="h",
        embedding_schema_version=1,
        embedding=vector,
    )


def _indexing_workflow(project_id: uuid.UUID) -> RetrievalIndexingWorkflow:
    return RetrievalIndexingWorkflow(
        session=_session(),
        project_id=project_id,
        embedder=_embedder(),
        embedding_set_version=1,
        filterable_metadata_keys=["source"],
    )


async def test_indexing_rejects_superseded_document_version() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.CHUNKED)
    workflow = _indexing_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))

    with pytest.raises(PermanentJobError):
        await workflow.run(document.id, expected_document_version=2)


async def test_indexing_fails_without_embeddings() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.INDEXING)
    workflow = _indexing_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._embedding_repository = _repo(list_by_document=AsyncMock(return_value=[]))

    with pytest.raises(StageFailure, match="No embeddings available"):
        await workflow.run(document.id)


async def test_indexing_happy_path_validates_embeddings_and_builds_keywords() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.INDEXING)
    chunk = _chunk(document, 0)
    embedding = _embedding_row(document, chunk)
    workflow = _indexing_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._embedding_repository = _repo(list_by_document=AsyncMock(return_value=[embedding]))

    with patch(
        "app.modules.retrieval.workflows.retrieval_indexing_workflow.KeywordIndexingWorkflow.index_document",
        new_callable=AsyncMock,
    ) as keyword_index:
        keyword_index.return_value = 1
        result = await workflow.run(document.id)
        keyword_index.assert_awaited_once()

    assert result is not None
    assert result.status is DocumentStatus.READY


async def test_indexing_chunk_count_mismatch_propagates() -> None:
    project_id = uuid.uuid4()
    document = _document(project_id, DocumentStatus.INDEXING)
    chunk = _chunk(document, 0)
    embedding = _embedding_row(document, chunk)
    workflow = _indexing_workflow(project_id)
    workflow._document_repository = _repo(get_by_id=AsyncMock(return_value=document))
    workflow._embedding_repository = _repo(list_by_document=AsyncMock(return_value=[embedding]))

    with (
        patch(
            "app.modules.retrieval.workflows.retrieval_indexing_workflow.KeywordIndexingWorkflow.index_document",
            new_callable=AsyncMock,
            return_value=0,
        ),
        pytest.raises(StageFailure, match="Embedding and keyword chunk counts differ"),
    ):
        await workflow.run(document.id)
