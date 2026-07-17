"""Shared audit vocabulary and recorder boundary."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class AuditEventType(StrEnum):
    JOB_SUBMITTED = "job.submitted"
    JOB_RETRIED = "job.retried"
    JOB_STARTED = "job.started"
    JOB_SUCCEEDED = "job.succeeded"
    JOB_RETRY_SCHEDULED = "job.retry_scheduled"
    JOB_FAILED = "job.failed"
    JOB_RECOVERED = "job.recovered"
    JOB_DISPATCH_DEFERRED = "job.dispatch_deferred"


class AuditActorType(StrEnum):
    SYSTEM = "system"
    OPERATOR = "operator"
    WORKER = "worker"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DEFERRED = "deferred"


class AuditRecorder(ABC):
    """Stage a sanitized, project-scoped audit record in the current transaction."""

    @abstractmethod
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
    ) -> None: ...
