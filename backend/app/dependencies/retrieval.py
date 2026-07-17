"""FastAPI dependencies for the Retrieval module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.retrieval import build_indexing_service
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.modules.retrieval.services.indexing_service import IndexingService
from app.modules.retrieval.services.search_service import SearchService
from app.platform.jobs.contracts import DurableJobSubmitter
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.implementations.reranker_factory import get_reranker_provider


def get_indexing_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    job_submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
    embedder: Annotated[BaseEmbeddingProvider, Depends(get_embedding_provider)],
) -> IndexingService:
    return build_indexing_service(
        session=session,
        project_id=project_id,
        settings=get_settings(),
        job_submitter=job_submitter,
        embedder=embedder,
    )


def get_search_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    embedder: Annotated[BaseEmbeddingProvider, Depends(get_embedding_provider)],
    reranker: Annotated[BaseRerankerProvider, Depends(get_reranker_provider)],
) -> SearchService:
    settings = get_settings()

    return SearchService(
        session=session,
        project_id=project_id,
        embedder=embedder,
        reranker=reranker,
        retrieval_config=settings.retrieval,
    )


IndexingServiceDep = Annotated[IndexingService, Depends(get_indexing_service)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
