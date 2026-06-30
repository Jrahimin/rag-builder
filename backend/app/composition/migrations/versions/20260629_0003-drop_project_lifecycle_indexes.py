"""drop redundant project lifecycle indexes

Revision ID: 0003_drop_project_idx
Revises: 0002_add_projects
Create Date: 2026-06-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_drop_project_idx"
down_revision: str | None = "0002_add_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_projects_is_active", table_name="projects")
    op.drop_index("ix_projects_deleted_at", table_name="projects")


def downgrade() -> None:
    op.create_index("ix_projects_deleted_at", "projects", ["deleted_at"], unique=False)
    op.create_index("ix_projects_is_active", "projects", ["is_active"], unique=False)
