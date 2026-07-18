"""Unit tests for IndexingService enqueue guards and rollback behaviour."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import RetrievalConfig, Settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.document import Document, DocumentStatus
from app.modules.retrieval.services.indexing_service import IndexingService
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobSubmission

pytestmark = pytest.mark.unit


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
def job_submitter() -> MagicMock:
    mock = MagicMock(spec=DurableJobSubmitter)
    mock.stage = AsyncMock(return_value=JobSubmission(job_id=uuid.uuid4(), created=True))
    mock.dispatch = AsyncMock()
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    project_id: uuid.UUID,
    job_submitter: MagicMock,
) -> IndexingService:
    svc = IndexingService(
        session=session,
        project_id=project_id,
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(
            Settings(embedding={"dimensions": 8, "model": "m"})
        ),
        retrieval_config=RetrievalConfig(),
        job_max_attempts=3,
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
    job_submitter: MagicMock,
) -> None:
    document = _document(project_id, DocumentStatus.CHUNKED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)

    result = await service.enqueue_embed(document.id)

    assert result.status is DocumentStatus.EMBEDDING
    job_submitter.stage.assert_awaited_once()
    job = job_submitter.stage.await_args.args[0]
    assert job.name == "document.embed"
    assert job.document_id == document.id
    assert job.payload == {
        "document_version": 1,
        "embedding_set_version": 1,
        "operation": "reembed",
    }
    job_submitter.dispatch.assert_awaited_once_with(result.job_id)


async def test_enqueue_index_rejects_unembedded_document(
    service: IndexingService,
    project_id: uuid.UUID,
) -> None:
    document = _document(project_id, DocumentStatus.CHUNKED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)

    with pytest.raises(BadRequestError) as exc_info:
        await service.enqueue_index(document.id)

    assert exc_info.value.code == "document_not_indexable"


async def test_enqueue_index_stages_durable_job(
    service: IndexingService,
    project_id: uuid.UUID,
    job_submitter: MagicMock,
) -> None:
    document = _document(project_id, DocumentStatus.EMBEDDED)
    service._document_repository.get_by_id = AsyncMock(return_value=document)
    result = await service.enqueue_index(document.id)

    assert result.status is DocumentStatus.INDEXING
    job_submitter.stage.assert_awaited_once()
    assert job_submitter.stage.await_args.args[0].name == "document.index"


async def test_enqueue_embed_if_enabled_respects_flag(
    session: AsyncMock,
    project_id: uuid.UUID,
    job_submitter: MagicMock,
) -> None:
    service = IndexingService(
        session=session,
        project_id=project_id,
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(
            Settings(embedding={"dimensions": 8, "model": "m"})
        ),
        retrieval_config=RetrievalConfig(auto_embed=False),
        job_max_attempts=3,
    )
    document = _document(project_id, DocumentStatus.CHUNKED)
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=document)
    service._document_repository = repository

    result = await service.enqueue_embed_if_enabled(document.id)

    assert result is document
    assert result.status is DocumentStatus.CHUNKED
    job_submitter.stage.assert_not_awaited()
