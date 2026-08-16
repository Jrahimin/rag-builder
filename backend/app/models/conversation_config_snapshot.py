"""Immutable effective AI configuration captured for a conversation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class ConversationConfigSnapshot(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Append-only effective policy for future messages in one conversation."""

    __tablename__ = "conversation_config_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_config_snapshot_sequence"
        ),
        Index(
            "ix_conversation_config_snapshots_project_conversation",
            "project_id",
            "conversation_id",
            "created_at",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    origins: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    compatibility_diagnostics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
