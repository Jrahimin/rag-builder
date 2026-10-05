"""Opt-in expiring operator evaluation payloads, never part of public run JSON."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db.base import Base
from app.platform.domain.mixins import ProjectScopedMixin, UUIDPrimaryKeyMixin


class EvaluationDiagnostic(Base, UUIDPrimaryKeyMixin, ProjectScopedMixin):
    __tablename__ = "evaluation_diagnostics"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        Index("ix_evaluation_diagnostics_project_run", "project_id", "run_id"),
        Index("ix_evaluation_diagnostics_expiry", "expires_at"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    case_key: Mapped[str] = mapped_column(String(128), nullable=False)
    profile: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
