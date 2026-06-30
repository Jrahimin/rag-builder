"""Shared SQLAlchemy filter and ordering helpers for repositories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import InstrumentedAttribute

from app.platform.db.base import Base


@dataclass(frozen=True, slots=True)
class LifecycleListFilters:
    """Standard list filters for entities with soft delete and active status."""

    include_deleted: bool = False
    is_active: bool | None = None


def build_lifecycle_filters(
    model: type[Base],
    *,
    include_deleted: bool,
    is_active: bool | None,
) -> list[ColumnElement[bool]]:
    """Build WHERE clauses for ``deleted_at`` and ``is_active`` when present on ``model``."""
    filters: list[ColumnElement[bool]] = []

    deleted_at = getattr(model, "deleted_at", None)
    if deleted_at is not None and not include_deleted:
        filters.append(deleted_at.is_(None))

    active_col = getattr(model, "is_active", None)
    if active_col is not None and is_active is not None:
        filters.append(active_col == is_active)

    return filters


def apply_deterministic_order[T: Base](
    stmt: Select[tuple[T]],
    model: type[T],
) -> Select[tuple[T]]:
    """Order by ``created_at``, then ``id`` when those columns exist."""
    created_at = getattr(model, "created_at", None)
    entity_id = getattr(model, "id", None)
    if created_at is not None and entity_id is not None:
        return stmt.order_by(created_at, entity_id)
    if entity_id is not None:
        return stmt.order_by(entity_id)
    return stmt


def not_deleted_filter(model: type[Base]) -> ColumnElement[bool]:
    """Require ``deleted_at IS NULL`` — raises if the model has no soft-delete column."""
    deleted_at = getattr(model, "deleted_at", None)
    if deleted_at is None:
        msg = f"{model.__name__} must compose SoftDeleteMixin for not_deleted_filter"
        raise TypeError(msg)
    return deleted_at.is_(None)


def column_equals(
    model: type[Base],
    field_name: str,
    value: object,
) -> ColumnElement[bool]:
    """Return ``model.field == value`` for a mapped column name."""
    column: InstrumentedAttribute[Any] | None = getattr(model, field_name, None)
    if column is None:
        msg = f"{model.__name__} has no column '{field_name}'"
        raise AttributeError(msg)
    return column == value
