"""Unit tests for IndexingService enqueue guards and rollback behaviour."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import RetrievalConfig
from app.core.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError
from app.models.document import Document, DocumentStatus
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.contracts import JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.memory_vector_store import MemoryVectorStoreProvider

pytestmark = pytest.mark.unit


async def _noop_ensure_project() -> None:
    return None


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


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def job_queue() -> AsyncMock:
    mock = AsyncMock(spec=JobQueue)
    mock.enqueue = AsyncMock(return_value="job-1")
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    project_id: uuid.UUID,
    job_queue: AsyncMock,
) -> IndexingService:
    svc = IndexingService(
        session=session,
        project_id=project_id,
        job_queue=job_queue,
        embedder=HashEmbeddingProvider(model="m", dimensions=8, provider_version="1"),
        vector_store=MemoryVectorStoreProvider(),
        retrieval_config=RetrievalConfig(),
        embedding_batch_size=32,
        filterable_metadata_keys=["source"],
        ensure_project=_noop_ensure_project,
    )
    repository = MagicMock()
    repository.flush = AsyncMock()
    svc._document_repository = repository
    return svc


async def test_enqueue_embed_requires_known_document(service: IndexingService) -> None:
    service._document_repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await service.enqueue_embed(uuid.uuid4())


async def test_enqueue_embed_rejects_wrong_status(
    service: IndexingService,
    project_id: uuid.UUID,
) -> None:
    document = _document(project_id, DocumentStatus.PARSING)
    service._document_repository.get_by_id = AsyncMock(return_value=document)

    with pytest.raises(BadRequestError) as exc_info:
        await service.enqueue_embed(document.id)

    assert exc_info.value.code == "document_not_embedable"


async def test_enqueue_embed_sets_status_and_enqueues(
    service: IndexingService,
    project_id: uuid.UUID,
    job_queue: AsyncMock,
) -> None:
    document = _document(project_id, DocumentStatus.CHUNKED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)

    result = await service.enqueue_embed(document.id)

    assert result.status is DocumentStatus.EMBEDDING
    job_queue.enqueue.assert_awaited_once()
    job = job_queue.enqueue.await_args.args[0]
    assert job.name == "document.embed"
    assert job.payload == {"document_id": str(document.id)}


async def test_enqueue_embed_rolls_back_status_on_queue_failure(
    service: IndexingService,
    project_id: uuid.UUID,
    job_queue: AsyncMock,
) -> None:
    document = _document(project_id, DocumentStatus.CHUNKED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)
    job_queue.enqueue.side_effect = JobEnqueueError("queue down")

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await service.enqueue_embed(document.id)

    assert exc_info.value.code == "job_queue_unavailable"
    assert document.status is DocumentStatus.CHUNKED


async def test_enqueue_index_rejects_unembedded_document(
    service: IndexingService,
    project_id: uuid.UUID,
) -> None:
    document = _document(project_id, DocumentStatus.CHUNKED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)

    with pytest.raises(BadRequestError) as exc_info:
        await service.enqueue_index(document.id)

    assert exc_info.value.code == "document_not_indexable"


async def test_enqueue_index_rolls_back_status_on_queue_failure(
    service: IndexingService,
    project_id: uuid.UUID,
    job_queue: AsyncMock,
) -> None:
    document = _document(project_id, DocumentStatus.EMBEDDED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)
    job_queue.enqueue.side_effect = JobEnqueueError("queue down")

    with pytest.raises(ServiceUnavailableError):
        await service.enqueue_index(document.id)

    assert document.status is DocumentStatus.EMBEDDED


async def test_enqueue_embed_if_enabled_respects_flag(
    session: AsyncMock,
    project_id: uuid.UUID,
    job_queue: AsyncMock,
) -> None:
    service = IndexingService(
        session=session,
        project_id=project_id,
        job_queue=job_queue,
        embedder=HashEmbeddingProvider(model="m", dimensions=8, provider_version="1"),
        vector_store=MemoryVectorStoreProvider(),
        retrieval_config=RetrievalConfig(auto_embed=False),
        embedding_batch_size=32,
        filterable_metadata_keys=[],
        ensure_project=_noop_ensure_project,
    )
    document = _document(project_id, DocumentStatus.CHUNKED)
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=document)
    service._document_repository = repository

    result = await service.enqueue_embed_if_enabled(document.id)

    assert result is document
    assert result.status is DocumentStatus.CHUNKED
    job_queue.enqueue.assert_not_awaited()
