"""Boundaries shared by webhook producers and delivery infrastructure."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class WebhookEventType(StrEnum):
    """The intentionally small, versioned hosted-integration event vocabulary."""

    DOCUMENT_PROCESSING_SUCCEEDED_V1 = "document.processing.succeeded.v1"
    DOCUMENT_PROCESSING_FAILED_V1 = "document.processing.failed.v1"
    DOCUMENT_INDEXING_SUCCEEDED_V1 = "document.indexing.succeeded.v1"
    DOCUMENT_INDEXING_FAILED_V1 = "document.indexing.failed.v1"


class WebhookDeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WebhookEventDefinition:
    event_type: WebhookEventType
    source_key: str
    source_type: str
    source_id: uuid.UUID
    data: dict[str, Any]
    occurred_at: datetime


class WebhookEventPublisher(ABC):
    """Stage an event and endpoint deliveries in the caller-owned transaction."""

    @abstractmethod
    async def stage(self, definition: WebhookEventDefinition) -> None: ...


class NullWebhookEventPublisher(WebhookEventPublisher):
    async def stage(self, definition: WebhookEventDefinition) -> None:
        del definition


@dataclass(frozen=True, slots=True)
class WebhookHttpResponse:
    status_code: int
    body: str
    latency_ms: float


class WebhookTransport(ABC):
    """HTTP transport boundary; vendor objects never leave implementations."""

    @abstractmethod
    async def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebhookHttpResponse: ...

