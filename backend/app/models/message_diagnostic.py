"""Project-scoped operator diagnostics, separate from public message metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class MessageDiagnostic(Base, UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin):
    __tablename__ = "message_diagnostics"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        Index("ix_message_diagnostics_project_message", "project_id", "message_id", unique=True),
        Index("ix_message_diagnostics_expiry", "expires_at"),
    )
    message_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
