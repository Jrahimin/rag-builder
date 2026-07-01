"""FastAPI dependencies for the Knowledge module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.exceptions import NotFoundError
from app.dependencies.common import DbSessionDep
from app.dependencies.projects import get_project_repository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.document_service import DocumentService
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.platform.jobs.contracts import JobQueue
from app.platform.jobs.implementations.job_queue_factory import get_job_queue
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.implementations.storage_factory import get_storage_provider


def get_document_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> DocumentRepository:
    return DocumentRepository(session, project_id)


def get_storage() -> BaseStorageProvider:
    return get_storage_provider()


async def _ensure_project_exists(
    project_repository: ProjectRepository,
    project_id: uuid.UUID,
) -> None:
    project = await project_repository.get_by_id(project_id)
    if project is None:
        raise NotFoundError(
            message="Project not found.",
            code="project_not_found",
        )


def get_job_queue_dep() -> JobQueue:
    return get_job_queue()


def get_document_service(
    session: DbSessionDep,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    storage: Annotated[BaseStorageProvider, Depends(get_storage)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue_dep)],
) -> DocumentService:
    project_id = repository.project_id

    async def ensure_project() -> None:
        await _ensure_project_exists(project_repository, project_id)

    return DocumentService(
        session=session,
        repository=repository,
        storage=storage,
        job_queue=job_queue,
        ensure_project=ensure_project,
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
