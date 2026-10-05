"""Append-only project/build quality attestations, independent of integrity state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class IndexAcceptance(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    __tablename__ = "index_acceptances"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["project_id", "build_id"],
            ["index_builds.project_id", "index_builds.id"],
            ondelete="CASCADE",
        ),
        Index("ix_index_acceptances_project_build", "project_id", "build_id", "created_at"),
        Index("ix_index_acceptances_project_hash", "project_id", "artifact_hash", unique=True),
    )
    build_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IndexAcceptanceSet(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """One immutable approved definition bound to a sealed project/build."""

    __tablename__ = "index_acceptance_sets"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["project_id", "build_id"],
            ["index_builds.project_id", "index_builds.id"],
            ondelete="CASCADE",
        ),
        Index("ix_index_acceptance_sets_project_build", "project_id", "build_id", unique=True),
    )
    build_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IndexAcceptanceReport(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    """Immutable observed production captures, source labels and comparison verdicts."""

    __tablename__ = "index_acceptance_reports"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["project_id", "build_id"],
            ["index_builds.project_id", "index_builds.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_index_acceptance_reports_hash", "project_id", "build_id", "report_hash", unique=True
        ),
    )
    build_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
