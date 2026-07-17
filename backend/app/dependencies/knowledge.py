"""FastAPI dependencies for the Knowledge module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.models.document import Document
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.retrieval.services.retrieval_cleanup_service import RetrievalCleanupService
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter
from app.platform.providers.contracts.storage import BaseStorageProvider
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
    project_id = repository.project_id

    cleanup = RetrievalCleanupService(
        session=session,
        project_id=project_id,
    )

    async def on_document_delete(document: Document) -> None:
        await cleanup.on_document_delete(document.id)

    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(settings),
        job_max_attempts=settings.jobs.max_attempts,
        max_upload_bytes=settings.knowledge.max_upload_bytes,
        on_document_delete=on_document_delete,
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
