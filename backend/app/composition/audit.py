"""Database implementation of the shared audit recorder contract."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)


class DatabaseAuditRecorder(AuditRecorder):
    """Add audit events to a caller-owned SQLAlchemy transaction."""

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    def record(
        self,
        *,
        event_type: AuditEventType,
        actor_type: AuditActorType,
        resource_type: str,
        resource_id: uuid.UUID,
        outcome: AuditOutcome,
        actor_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditEvent(
                project_id=self._project_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                detail=detail or {},
            )
        )
