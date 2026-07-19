"""Stable API schemas for outbound webhooks."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.platform.webhooks.contracts import WebhookDeliveryState, WebhookEventType


class WebhookEndpointCreate(BaseModel):
    url: AnyHttpUrl = Field(examples=["https://customer.example.com/webhooks/ape"])
    description: str | None = Field(default=None, max_length=255)
    event_types: list[WebhookEventType] = Field(
        min_length=1,
        examples=[[WebhookEventType.DOCUMENT_INDEXING_SUCCEEDED_V1]],
    )

    @field_validator("url")
    @classmethod
    def _reject_url_secrets_and_fragments(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("webhook URLs must not contain credentials")
        if value.fragment is not None:
            raise ValueError("webhook URLs must not contain fragments")
        return value

    @field_validator("event_types")
    @classmethod
    def _deduplicate_events(cls, value: list[WebhookEventType]) -> list[WebhookEventType]:
        return list(dict.fromkeys(value))


class WebhookEndpointStatusUpdate(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=255)


class WebhookEndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    url: str
    description: str | None
    event_types: list[WebhookEventType]
    is_enabled: bool
    disabled_at: datetime | None
    disabled_reason: str | None
    created_at: datetime
    updated_at: datetime


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    signing_secret: str = Field(
        description="Endpoint signing secret. Store it securely and use it to verify signatures."
    )


class WebhookEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    event_type: WebhookEventType
    api_version: int
    source_type: str
    source_id: uuid.UUID
    data: dict[str, object]
    occurred_at: datetime
    created_at: datetime


class WebhookAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_number: int
    attempted_at: datetime
    status_code: int | None
    latency_ms: float | None
    error: str | None
    response_excerpt: str | None


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    replay_of_delivery_id: uuid.UUID | None
    replay_number: int
    state: WebhookDeliveryState
    attempt_count: int
    max_attempts: int
    available_at: datetime
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryDetailResponse(WebhookDeliveryResponse):
    event: WebhookEventResponse
    attempts: list[WebhookAttemptResponse]
