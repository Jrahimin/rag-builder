"""Ownership scope — where a resource or setting logically belongs.

Used in documentation and future configuration APIs. Project-owned business data
is enforced at the persistence layer via :class:`ProjectScopedRepository` and
:class:`ProjectScopedMixin`, not through catalog enums.
"""

from __future__ import annotations

from enum import StrEnum


class OwnershipScope(StrEnum):
    """Logical ownership boundary within a deployment."""

    DEPLOYMENT = "deployment"
    """Infrastructure and process configuration for one APE instance."""

    PLATFORM = "platform"
    """Deployment-wide defaults shared across Projects."""

    PROJECT = "project"
    """Business data and per-Project AI configuration (default)."""
