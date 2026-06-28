"""initial baseline

Establishes the migration chain. No business tables are created in the
foundation sprint; concrete entities (projects, documents, ...) arrive in
later sprints as their own revisions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op baseline."""


def downgrade() -> None:
    """No-op baseline."""
