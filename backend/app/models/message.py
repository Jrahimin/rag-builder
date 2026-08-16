"""Message ORM entity — project-scoped chat turn."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ProjectScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class MessageRole(StrEnum):
    """Chat message roles."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    """A single message in a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["config_snapshot_id"],
            ["conversation_config_snapshots.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_messages_project_conversation", "project_id", "conversation_id"),
        Index("ix_messages_project_usage_created", "project_id", "role", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", native_enum=False),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding_set_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    index_build_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    source_metadata_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    citations: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    claims: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    insufficient_evidence_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
