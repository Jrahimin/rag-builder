"""FastAPI dependencies for the Knowledge module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.projects import ensure_project_exists, get_project_repository
from app.models.document import Document
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.retrieval.services.retrieval_cleanup_service import RetrievalCleanupService
from app.platform.jobs.contracts import JobQueue
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.implementations.storage_factory import get_storage_provider
from app.platform.providers.implementations.vector_store_factory import get_vector_store_provider


def get_document_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> DocumentRepository:
    return DocumentRepository(session, project_id)


def get_storage() -> BaseStorageProvider:
    return get_storage_provider()


def get_job_queue_dep() -> JobQueue:
    return get_job_queue()


def get_document_service(
    session: DbSessionDep,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    storage: Annotated[BaseStorageProvider, Depends(get_storage)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue_dep)],
) -> DocumentService:
    settings = get_settings()
    project_id = repository.project_id

    async def ensure_project() -> None:
        await ensure_project_exists(project_repository, project_id)

    cleanup = RetrievalCleanupService(
        session=session,
        project_id=project_id,
        vector_store=get_vector_store_provider(),
    )

    async def on_document_delete(document: Document) -> None:
        await cleanup.on_document_delete(document.id)

    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_queue=job_queue,
        ensure_project=ensure_project,
        max_upload_bytes=settings.knowledge.max_upload_bytes,
        on_document_delete=on_document_delete,
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
