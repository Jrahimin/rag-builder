"""Immutable webhook events and auditable delivery attempts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.platform.webhooks.contracts import WebhookDeliveryState, WebhookEventType


class WebhookEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    __tablename__ = "webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        UniqueConstraint("project_id", "source_key", name="uq_webhook_events_project_source"),
        Index("ix_webhook_events_project_created", "project_id", "created_at", "id"),
    )

    event_type: Mapped[WebhookEventType] = mapped_column(
        Enum(WebhookEventType, name="webhook_event_type", native_enum=False, length=64),
        nullable=False,
    )
    api_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["event_id"], ["webhook_events.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["replay_of_delivery_id"], ["webhook_deliveries.id"], ondelete="SET NULL"
        ),
        UniqueConstraint(
            "endpoint_id", "event_id", "replay_number", name="uq_webhook_delivery_replay"
        ),
        Index("ix_webhook_deliveries_project_created", "project_id", "created_at", "id"),
        Index("ix_webhook_deliveries_dispatch", "state", "available_at", "lease_expires_at"),
        Index("ix_webhook_deliveries_endpoint", "project_id", "endpoint_id", "created_at"),
    )

    endpoint_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    replay_of_delivery_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    replay_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    state: Mapped[WebhookDeliveryState] = mapped_column(
        Enum(
            WebhookDeliveryState,
            name="webhook_delivery_state",
            native_enum=False,
            length=32,
        ),
        nullable=False,
        default=WebhookDeliveryState.PENDING,
        server_default=WebhookDeliveryState.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookDeliveryAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    __tablename__ = "webhook_delivery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["delivery_id"], ["webhook_deliveries.id"], ondelete="CASCADE"),
        UniqueConstraint("delivery_id", "attempt_number", name="uq_webhook_attempt_number"),
        Index("ix_webhook_attempts_delivery", "project_id", "delivery_id", "created_at"),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
