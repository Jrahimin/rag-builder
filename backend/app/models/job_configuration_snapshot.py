"""Immutable normalized processing/index configuration snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class JobConfigurationSnapshot(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Content-addressed configuration used by one or more durable jobs.

    Snapshots intentionally have no ``updated_at`` column and no update path.
    A configuration change creates a new hash-addressed row.
    """

    __tablename__ = "job_configuration_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "project_id",
            "configuration_hash",
            name="uq_job_configuration_snapshots_project_hash",
        ),
        Index("ix_job_configuration_snapshots_project_created", "project_id", "created_at"),
    )

    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
