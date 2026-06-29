"""Reusable ORM mixins — building blocks for module-owned entities."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key (``id``)."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` audit timestamps (UTC, server-side)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProjectScopedMixin:
    """Adds an indexed ``project_id`` — mandatory for Project-owned entities.

    Every query, job, and deletion on Project-owned data must filter by this
    column. Foreign keys to ``projects`` are declared on concrete module models.
    """

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        index=True,
    )
