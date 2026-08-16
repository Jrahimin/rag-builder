"""Conversation ORM entity — project-scoped chat session."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import (
    ActiveStatusMixin,
    ProjectScopedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Conversation(
    Base,
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    ActiveStatusMixin,
    ProjectScopedMixin,
):
    """A multi-turn chat session within a Project."""

    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversations_project_last_message", "project_id", "last_message_at"),
    )

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    system_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active_config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "conversation_config_snapshots.id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
