"""FastAPI dependencies for the Retrieval module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.audit import DatabaseAuditRecorder
from app.composition.retrieval import build_indexing_service
from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.models.index_build import ProjectIndexPointer
from app.models.project import Project
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.modules.retrieval.services.index_lifecycle_service import IndexLifecycleService
from app.modules.retrieval.services.indexing_service import IndexingService
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.implementations.reranker_factory import get_reranker_provider


async def get_indexing_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    job_submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
) -> IndexingService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    resolution = resolve_project_ai_config(
        settings,
        ConfigRevisionRecord(
            id=revision.id,
            revision_number=revision.revision_number,
            configuration_hash=revision.configuration_hash,
            configuration=dict(revision.configuration),
        )
        if revision is not None
        else None,
    )
    project = await session.get(Project, project_id)
    pointer = await session.get(ProjectIndexPointer, project_id)
    return build_indexing_service(
        session=session,
        project_id=project_id,
        settings=settings,
        job_submitter=job_submitter,
        resolution=resolution,
        active_index_build_id=(
            str(pointer.active_build_id) if pointer and pointer.active_build_id else None
        ),
        source_metadata_generation=(
            project.source_metadata_generation if project is not None else 0
        ),
    )


async def get_search_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    embedder: Annotated[BaseEmbeddingProvider, Depends(get_embedding_provider)],
    reranker: Annotated[BaseRerankerProvider, Depends(get_reranker_provider)],
) -> SearchService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    resolution = resolve_project_ai_config(
        settings,
        ConfigRevisionRecord(
            id=revision.id,
            revision_number=revision.revision_number,
            configuration_hash=revision.configuration_hash,
            configuration=dict(revision.configuration),
        )
        if revision is not None
        else None,
    )
    effective = apply_effective_ai_config(settings, resolution)

    return SearchService(
        session=session,
        project_id=project_id,
        embedder=embedder,
        reranker=reranker,
        retrieval_config=effective.retrieval,
        ai_policy=settings.ai_policy,
        source_metadata=KnowledgeRetrievalSourceMetadataAdapter(session),
        configured_source_policy_mode=resolution.provenance.configured_source_policy_mode,
        configuration_hash=resolution.configuration_hash,
        config_provenance=resolution.provenance.model_dump(mode="json"),
    )


async def get_index_lifecycle_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    job_submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
) -> IndexLifecycleService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    resolution = resolve_project_ai_config(
        settings,
        ConfigRevisionRecord(
            id=revision.id,
            revision_number=revision.revision_number,
            configuration_hash=revision.configuration_hash,
            configuration=dict(revision.configuration),
        )
        if revision is not None
        else None,
    )
    project = await session.get(Project, project_id)
    pointer = await session.get(ProjectIndexPointer, project_id)
    return IndexLifecycleService(
        session=session,
        project_id=project_id,
        job_submitter=job_submitter,
        job_configuration=build_job_configuration(
            settings,
            resolution=resolution,
            active_index_build_id=(
                str(pointer.active_build_id) if pointer and pointer.active_build_id else None
            ),
            source_metadata_generation=(
                project.source_metadata_generation if project is not None else 0
            ),
        ),
        embedding_set_version=settings.retrieval.embedding_set_version,
        job_max_attempts=settings.jobs.max_attempts,
        audit=DatabaseAuditRecorder(session, project_id),
    )


IndexingServiceDep = Annotated[IndexingService, Depends(get_indexing_service)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
IndexLifecycleServiceDep = Annotated[IndexLifecycleService, Depends(get_index_lifecycle_service)]
