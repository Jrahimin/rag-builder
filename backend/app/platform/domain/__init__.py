"""Shared domain primitives — ORM mixins, ownership scope, service helpers.

Concrete ORM models live in ``app.models``. Register them in
``app.composition.orm_registry`` for Alembic — not here.
"""

from app.platform.domain.mixins import ProjectScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.platform.domain.ownership import OwnershipScope

__all__ = [
    "OwnershipScope",
    "ProjectScopedMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
