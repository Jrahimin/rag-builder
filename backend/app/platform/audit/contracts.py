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
    INDEX_BUILD_ACTIVATED = "index_build.activated"
    INDEX_BUILD_ROLLED_BACK = "index_build.rolled_back"
    DOCUMENT_DELETE_REQUESTED = "document.delete_requested"
    DOCUMENT_PURGE_REQUESTED = "document.purge_requested"
    STORAGE_RECONCILIATION_REQUESTED = "storage.reconciliation_requested"
    WEBHOOK_ENDPOINT_CREATED = "webhook.endpoint_created"
    WEBHOOK_ENDPOINT_ENABLED = "webhook.endpoint_enabled"
    WEBHOOK_ENDPOINT_DISABLED = "webhook.endpoint_disabled"
    WEBHOOK_DELIVERY_REPLAYED = "webhook.delivery_replayed"


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
