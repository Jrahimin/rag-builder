"""Contextual generation ORM entity — Project-scoped execution trace."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import GenerationRetentionMode
from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class GenerationStatus(StrEnum):
    """Persisted lifecycle for one synchronous generation request."""

    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GroundingStatus(StrEnum):
    """How the generation input was grounded."""

    CONTEXT_SUPPLIED = "context_supplied"
    FAILED = "failed"


class Generation(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """Auditable contextual generation result and provider usage trace."""

    __tablename__ = "generations"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "project_id",
            "idempotency_key_hash",
            name="uq_generations_project_idempotency",
        ),
        Index("ix_generations_project_created", "project_id", "created_at", "id"),
        Index("ix_generations_project_status", "project_id", "status", "created_at"),
    )

    use_case: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(
            GenerationStatus,
            name="generation_status",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=GenerationStatus.PROCESSING,
        server_default=GenerationStatus.PROCESSING.value,
    )
    grounding_status: Mapped[GroundingStatus] = mapped_column(
        Enum(
            GroundingStatus,
            name="generation_grounding_status",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=GroundingStatus.CONTEXT_SUPPLIED,
        server_default=GroundingStatus.CONTEXT_SUPPLIED.value,
    )
    grounded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    response_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locale: Mapped[str | None] = mapped_column(String(35), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_build_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    source_metadata_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    config_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    retention_mode: Mapped[GenerationRetentionMode] = mapped_column(
        Enum(
            GenerationRetentionMode,
            name="generation_retention_mode",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    retained_input: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    retained_context: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    payload_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    output: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
