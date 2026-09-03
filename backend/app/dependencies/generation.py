"""FastAPI dependencies for contextual generation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.models.index_build import ProjectIndexPointer
from app.models.project import Project
from app.modules.generation.repositories.generation_repository import GenerationRepository
from app.modules.generation.services.generation_service import GenerationService
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.platform.config.project_ai import config_revision_record
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.implementations.llm_factory import (
    create_llm_provider_for_config,
)


def get_generation_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> GenerationRepository:
    return GenerationRepository(session, project_id)


async def get_generation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    repository: Annotated[
        GenerationRepository,
        Depends(get_generation_repository),
    ],
) -> GenerationService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    project = await session.get(Project, project_id)
    pointer = await session.get(ProjectIndexPointer, project_id)

    def resolve_llm(provider: str | None, model: str | None) -> BaseLLMProvider:
        return create_llm_provider_for_config(
            settings,
            provider=provider,
            model=model,
        )

    return GenerationService(
        session=session,
        project_id=project_id,
        repository=repository,
        generation_config=settings.generation,
        llm_config=settings.llm,
        resolve_llm=resolve_llm,
        settings=settings,
        active_revision=config_revision_record(revision),
        execution_provenance={
            "active_index_build_id": (
                str(pointer.active_build_id) if pointer and pointer.active_build_id else None
            ),
            "source_metadata_generation": (
                project.source_metadata_generation if project is not None else 0
            ),
        },
    )


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]
