"""Add Admin role support and soft-delete columns on admin_users.

Revision ID: 0027_admin_users_lifecycle
Revises: 0026_multilingual_embeddings
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_admin_users_lifecycle"
down_revision: str | None = "0026_multilingual_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("admin_users", sa.Column("deleted_by", sa.Uuid(), nullable=True))
    op.drop_index("uq_admin_users_email", table_name="admin_users")
    op.create_index(
        "uq_admin_users_email",
        "admin_users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_admin_users_email",
        table_name="admin_users",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("uq_admin_users_email", "admin_users", ["email"], unique=True)
    op.drop_column("admin_users", "deleted_by")
    op.drop_column("admin_users", "deleted_at")
