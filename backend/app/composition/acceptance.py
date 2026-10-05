"""Bind retrieval-owned report publication to the existing evaluation quality metrics."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evaluation.metrics import compute_message_acceptance_metrics
from app.modules.retrieval.services.observed_acceptance_service import ObservedAcceptanceService


def observed_acceptance_service(
    session: AsyncSession, project_id: uuid.UUID
) -> ObservedAcceptanceService:
    return ObservedAcceptanceService(
        session, project_id, message_metrics=compute_message_acceptance_metrics
    )
