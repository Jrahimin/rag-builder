"""FastAPI dependencies for the Retrieval module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.knowledge import get_job_queue_dep
from app.dependencies.projects import ensure_project_exists, get_project_repository
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.retrieval.services.indexing_service import IndexingService
from app.modules.retrieval.services.search_service import SearchService
from app.platform.jobs.contracts import JobQueue
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.implementations.vector_store_factory import get_vector_store_provider


def get_indexing_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    job_queue: Annotated[JobQueue, Depends(get_job_queue_dep)],
) -> IndexingService:
    async def ensure_project() -> None:
        await ensure_project_exists(project_repository, project_id)

    return IndexingService.from_settings(
        session=session,
        project_id=project_id,
        settings=get_settings(),
        ensure_project=ensure_project,
        job_queue=job_queue,
    )


def get_search_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> SearchService:
    settings = get_settings()

    async def ensure_project() -> None:
        await ensure_project_exists(project_repository, project_id)

    return SearchService(
        session=session,
        project_id=project_id,
        embedder=get_embedding_provider(),
        vector_store=get_vector_store_provider(),
        retrieval_config=settings.retrieval,
        ensure_project=ensure_project,
    )


IndexingServiceDep = Annotated[IndexingService, Depends(get_indexing_service)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
