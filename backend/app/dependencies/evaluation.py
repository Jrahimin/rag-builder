"""FastAPI composition for Project-scoped quality evaluation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.evaluation import build_evaluation_service
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.models.project import Project
from app.modules.evaluation.services.evaluation_service import EvaluationService
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.platform.config.project_ai import config_revision_record, resolve_project_ai_config
from app.platform.jobs.contracts import DurableJobSubmitter


async def get_evaluation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
) -> EvaluationService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    resolution = resolve_project_ai_config(
        settings,
            config_revision_record(revision),
    )
    project = await session.get(Project, project_id)
    return build_evaluation_service(
        session=session,
        project_id=project_id,
        settings=settings,
        submitter=submitter,
        resolution=resolution,
        source_metadata_generation=(
            project.source_metadata_generation if project is not None else 0
        ),
    )


EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
