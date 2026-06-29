"""Persistence primitives for feature module repositories."""

from app.platform.persistence.async_repository import AsyncRepository
from app.platform.persistence.filters import LifecycleListFilters
from app.platform.persistence.lifecycle import is_soft_deleted, mark_soft_deleted
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository

__all__ = [
    "AsyncRepository",
    "LifecycleListFilters",
    "ProjectScopedRepository",
    "is_soft_deleted",
    "mark_soft_deleted",
]
