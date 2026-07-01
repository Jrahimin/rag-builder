"""Unit tests for DocumentService."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.models.document import Document, DocumentStatus
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.schemas.document import DocumentIngestInput
from app.modules.knowledge.services.document_service import DocumentService
from app.platform.jobs.contracts import JobQueue
from app.platform.providers.contracts.storage import BaseStorageProvider

pytestmark = pytest.mark.unit


async def _noop_ensure_project() -> None:
    return None


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def repository(project_id: uuid.UUID) -> AsyncMock:
    mock = MagicMock(spec=DocumentRepository)
    mock.project_id = project_id
    mock.add = MagicMock(side_effect=lambda entity: entity)
    mock.exists_by_content_sha256 = AsyncMock(return_value=False)
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def storage() -> AsyncMock:
    mock = AsyncMock(spec=BaseStorageProvider)
    mock.put = AsyncMock()
    mock.delete = AsyncMock()
    return mock


@pytest.fixture
def job_queue() -> AsyncMock:
    mock = AsyncMock(spec=JobQueue)
    mock.enqueue = AsyncMock(return_value="job-1")
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    repository: AsyncMock,
    storage: AsyncMock,
    job_queue: AsyncMock,
) -> DocumentService:
    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_queue=job_queue,
        ensure_project=_noop_ensure_project,
    )


async def test_upload_persists_and_stores_bytes(
    service: DocumentService,
    session: AsyncMock,
    repository: AsyncMock,
    storage: AsyncMock,
    job_queue: AsyncMock,
) -> None:
    content = b"hello knowledge"
    documents: list[Document] = []

    def _add(entity: Document) -> Document:
        documents.append(entity)
        return entity

    repository.add.side_effect = _add
    repository.get_by_id = AsyncMock(side_effect=lambda *_a, **_k: documents[-1] if documents else None)

    result = await service.upload(
        DocumentIngestInput(
            filename="notes.txt",
            content_type="text/plain",
            stream=_stream(content),
        )
    )

    assert result.filename == "notes.txt"
    assert result.status == DocumentStatus.QUEUED
    assert result.version == 1
    assert result.size_bytes == len(content)
    repository.add.assert_called_once()
    storage.put.assert_awaited_once()
    job_queue.enqueue.assert_awaited_once()
    assert session.commit.await_count >= 2


async def test_upload_unknown_project_raises_not_found(
    session: AsyncMock,
    repository: AsyncMock,
    storage: AsyncMock,
) -> None:
    async def missing_project() -> None:
        raise NotFoundError(message="Project not found.", code="project_not_found")

    service = DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_queue=AsyncMock(spec=JobQueue),
        ensure_project=missing_project,
    )

    with pytest.raises(NotFoundError) as exc_info:
        await service.upload(
            DocumentIngestInput(
                filename="x.txt",
                content_type="text/plain",
                stream=_stream(b"x"),
            )
        )

    assert exc_info.value.code == "project_not_found"


async def test_upload_duplicate_content_raises_conflict(
    service: DocumentService,
    repository: AsyncMock,
) -> None:
    repository.exists_by_content_sha256.return_value = True

    with pytest.raises(ConflictError) as exc_info:
        await service.upload(
            DocumentIngestInput(
                filename="dup.txt",
                content_type="text/plain",
                stream=_stream(b"same"),
            )
        )

    assert exc_info.value.code == "document_content_duplicate"


async def test_get_raises_not_found(service: DocumentService, repository: AsyncMock) -> None:
    repository.get_by_id.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        await service.get(uuid.uuid4())

    assert exc_info.value.code == "document_not_found"


async def test_soft_delete_removes_storage(
    service: DocumentService,
    repository: AsyncMock,
    storage: AsyncMock,
    session: AsyncMock,
) -> None:
    document = Document(
        id=uuid.uuid4(),
        project_id=repository.project_id,
        filename="gone.txt",
        content_type="text/plain",
        size_bytes=3,
        storage_key="p/d/gone.txt",
        content_sha256="abc",
        status=DocumentStatus.UPLOADED,
        version=1,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository.get_by_id.return_value = document
    repository.flush = AsyncMock()

    result = await service.soft_delete(document.id)

    assert result.deleted_at is not None
    storage.delete.assert_awaited_once_with("p/d/gone.txt")
    session.commit.assert_awaited_once()
