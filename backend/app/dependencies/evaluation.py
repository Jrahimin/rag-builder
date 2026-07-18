"""FastAPI composition for Project-scoped quality evaluation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.evaluation import build_evaluation_service
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.jobs import get_job_submitter
from app.modules.evaluation.services.evaluation_service import EvaluationService
from app.platform.jobs.contracts import DurableJobSubmitter


def get_evaluation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    submitter: Annotated[DurableJobSubmitter, Depends(get_job_submitter)],
) -> EvaluationService:
    return build_evaluation_service(
        session=session,
        project_id=project_id,
        settings=get_settings(),
        submitter=submitter,
    )


EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
