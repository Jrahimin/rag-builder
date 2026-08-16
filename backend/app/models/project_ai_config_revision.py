"""Immutable typed Project AI-configuration revision."""

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


class ProjectAIConfigRevision(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Append-only Project policy; activation happens through the Project pointer."""

    __tablename__ = "project_ai_config_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "project_id", "revision_number", name="uq_project_ai_config_revision_number"
        ),
        Index("ix_project_ai_config_revisions_project_created", "project_id", "created_at"),
    )

    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    restored_from_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
