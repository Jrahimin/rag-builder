"""FastAPI composition for project-scoped webhook operations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.composition.audit import DatabaseAuditRecorder
from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.modules.webhooks.services.webhook_service import WebhookService


def get_webhook_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> WebhookService:
    settings = get_settings()
    return WebhookService(
        session,
        project_id,
        settings.webhooks,
        environment=settings.app.env,
        audit=DatabaseAuditRecorder(session, project_id),
    )


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]
