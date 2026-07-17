"""Reusable ORM mixins — building blocks for all entities in ``app.models``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Uuid, func, text
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


class ActiveStatusMixin:
    """Adds ``is_active`` for operational enable/disable without soft delete."""

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )


class SoftDeleteMixin:
    """Adds soft-delete columns; ``deleted_at`` is the persistence source of truth."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class ProjectScopedMixin:
    """Adds ``project_id`` — mandatory for Project-owned entities.

    Every query, job, and deletion on Project-owned data must filter by this
    column. Foreign keys and workload-appropriate indexes are declared on
    concrete models so the mixin does not create redundant single-column
    indexes beside their composite scope indexes.
    """

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
