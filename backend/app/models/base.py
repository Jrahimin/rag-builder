"""Reusable ORM mixins.

These are building blocks, not business entities. Concrete models (Project,
Document, ...) will be added in later sprints and compose these mixins to get
consistent primary keys, timestamps, and Project-scoping for free.
"""

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
    """Adds an indexed ``project_id`` to enforce Project-scoped isolation.

    Every Project-owned entity composes this mixin so retrieval, jobs, and
    deletions can always filter by ``project_id``. The foreign key to the
    ``projects`` table is declared on concrete models once that table exists.
    """

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        index=True,
    )
