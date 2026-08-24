"""Shared audit vocabulary and recorder boundary."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class AuditEventType(StrEnum):
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_STATUS_CHANGED = "organization.status_changed"
    ORGANIZATION_ARCHIVED = "organization.archived"
    ORGANIZATION_RESTORED = "organization.restored"
    API_KEY_CREATED = "api_key.created"
    API_KEY_ROTATED = "api_key.rotated"
    API_KEY_REVOKED = "api_key.revoked"
    ADMIN_USER_CREATED = "admin_user.created"
    ADMIN_USER_STATUS_CHANGED = "admin_user.status_changed"
    ADMIN_USER_ARCHIVED = "admin_user.archived"
    ADMIN_USER_RESTORED = "admin_user.restored"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_STATUS_CHANGED = "project.status_changed"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_OWNERSHIP_REASSIGNED = "project.ownership_reassigned"
    PROJECT_OWNERSHIP_CONFIRMED = "project.ownership_confirmed"
    PROJECT_CONFIG_REVISION_CREATED = "project_config.revision_created"
    PROJECT_CONFIG_REVISION_RESTORED = "project_config.revision_restored"
    CONVERSATION_CONFIG_UPDATED = "conversation.config_updated"
    SOURCE_METADATA_REVISION_CREATED = "source_metadata.revision_created"
    SOURCE_METADATA_REVISION_ACTIVATED = "source_metadata.revision_activated"
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
    """Stage a sanitized administrative event in the current transaction."""

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
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> None: ...
