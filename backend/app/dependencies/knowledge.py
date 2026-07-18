"""FastAPI dependencies for the Knowledge module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.audit import DatabaseAuditRecorder
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.knowledge.services.file_validation_service import FileValidationService
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.implementations.malware_scanner_provider import create_malware_scanner
from app.platform.providers.implementations.storage_factory import get_storage_provider


def get_document_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> DocumentRepository:
    return DocumentRepository(session, project_id)


def get_storage() -> BaseStorageProvider:
    return get_storage_provider()


def get_document_service(
    session: DbSessionDep,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[BaseStorageProvider, Depends(get_storage)],
    job_submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
) -> DocumentService:
    settings = get_settings()
    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        malware_scanner=create_malware_scanner(settings),
        file_validator=FileValidationService(),
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(settings),
        job_max_attempts=settings.jobs.max_attempts,
        max_upload_bytes=settings.knowledge.max_upload_bytes,
        audit=DatabaseAuditRecorder(session, repository.project_id),
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
