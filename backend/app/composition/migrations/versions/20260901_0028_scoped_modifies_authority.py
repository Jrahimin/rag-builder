"""add provision scope to modifies relationships

Revision ID: 0028_scoped_modifies
Revises: 0027_admin_users_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_scoped_modifies"
down_revision: str | None = "0027_admin_users_lifecycle"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "source_revision_relationships",
        sa.Column(
            "target_provisions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("source_revision_relationships", "target_provisions")
