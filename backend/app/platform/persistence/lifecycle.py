"""Shared entity lifecycle helpers for services and repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol


class _SoftDeletable(Protocol):
    deleted_at: datetime | None
    deleted_by: uuid.UUID | None


def mark_soft_deleted(
    entity: _SoftDeletable,
    *,
    deleted_by: uuid.UUID | None = None,
    deactivate: bool = True,
) -> None:
    """Set soft-delete fields on entities that compose ``SoftDeleteMixin``."""
    entity.deleted_at = datetime.now(UTC)
    entity.deleted_by = deleted_by

    if deactivate and hasattr(entity, "is_active"):
        entity.is_active = False


def is_soft_deleted(entity: object) -> bool:
    """Return whether ``entity`` has a non-null ``deleted_at``."""
    if not hasattr(entity, "deleted_at"):
        return False
    deleted_at = entity.deleted_at
    return deleted_at is not None
