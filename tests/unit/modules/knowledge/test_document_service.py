"""Unit tests for DocumentService."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.document import Document, DocumentStatus
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.schemas.document import DocumentIngestInput
from app.modules.knowledge.services.document_service import DocumentService
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobSubmission
from app.platform.providers.contracts.malware_scanner import MalwareScanResult
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderConnectionError

pytestmark = pytest.mark.unit


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
def job_submitter() -> MagicMock:
    mock = MagicMock(spec=DurableJobSubmitter)
    mock.stage = AsyncMock(return_value=JobSubmission(job_id=uuid.uuid4(), created=True))
    mock.dispatch = AsyncMock()
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    repository: AsyncMock,
    storage: AsyncMock,
    job_submitter: MagicMock,
) -> DocumentService:
    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(Settings()),
        job_max_attempts=3,
        max_upload_bytes=50 * 1024 * 1024,
    )


async def test_upload_persists_and_stores_bytes(
    service: DocumentService,
    session: AsyncMock,
    repository: AsyncMock,
    storage: AsyncMock,
    job_submitter: MagicMock,
) -> None:
    content = b"hello knowledge"
    documents: list[Document] = []

    def _add(entity: Document) -> Document:
        documents.append(entity)
        return entity

    repository.add.side_effect = _add
    repository.get_by_id = AsyncMock(
        side_effect=lambda *_a, **_k: documents[-1] if documents else None
    )

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
    job_submitter.stage.assert_awaited_once()
    definition = job_submitter.stage.await_args.args[0]
    assert definition.name == "document.process"
    assert definition.document_id == result.id
    assert definition.payload == {"document_version": 1, "operation": "ingest"}
    job_submitter.dispatch.assert_awaited_once_with(result.job_id)
    session.commit.assert_awaited_once()


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


async def test_upload_malware_fails_before_storage_or_job(
    service: DocumentService,
    storage: AsyncMock,
    job_submitter: MagicMock,
) -> None:
    service._malware_scanner.scan = AsyncMock(
        return_value=MalwareScanResult(clean=False, scanner="clamav", signature="Eicar-Test")
    )

    with pytest.raises(BadRequestError) as exc_info:
        await service.upload(
            DocumentIngestInput(
                filename="unsafe.txt", content_type="text/plain", stream=_stream(b"payload")
            )
        )

    assert exc_info.value.code == "document_malware_detected"
    storage.put.assert_not_awaited()
    job_submitter.stage.assert_not_awaited()


async def test_upload_fails_closed_when_scanner_is_unavailable(
    service: DocumentService,
    storage: AsyncMock,
    job_submitter: MagicMock,
) -> None:
    service._malware_scanner.scan = AsyncMock(
        side_effect=ProviderConnectionError("offline", provider_name="clamav")
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await service.upload(
            DocumentIngestInput(
                filename="safe.txt", content_type="text/plain", stream=_stream(b"payload")
            )
        )

    assert exc_info.value.code == "malware_scanner_unavailable"
    storage.put.assert_not_awaited()
    job_submitter.stage.assert_not_awaited()


async def test_get_raises_not_found(service: DocumentService, repository: AsyncMock) -> None:
    repository.get_by_id.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        await service.get(uuid.uuid4())

    assert exc_info.value.code == "document_not_found"


async def test_soft_delete_is_staged_as_durable_job(
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

    assert result.deleted_at is None
    assert result.status is DocumentStatus.DELETING
    definition = service._job_submitter.stage.await_args.args[0]
    assert definition.name == "document.delete"
    assert definition.document_id == document.id
    storage.delete.assert_not_awaited()
    storage.delete_document_tree.assert_not_awaited()
    session.commit.assert_awaited_once()
