"""FastAPI dependencies for contextual generation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.modules.generation.repositories.generation_repository import GenerationRepository
from app.modules.generation.services.generation_service import GenerationService
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.implementations.llm_factory import (
    create_llm_provider_for_config,
)


def get_generation_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> GenerationRepository:
    return GenerationRepository(session, project_id)


def get_generation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    repository: Annotated[
        GenerationRepository,
        Depends(get_generation_repository),
    ],
) -> GenerationService:
    settings = get_settings()

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
    )


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]
