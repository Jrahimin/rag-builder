"""add immutable index profile identity

Revision ID: 0030_config_phase2
Revises: 0029_config_phase1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0030_config_phase2"
down_revision: str | None = "0029_config_phase1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("index_builds", sa.Column("index_profile_id", sa.String(length=128)))
    op.add_column("index_builds", sa.Column("index_profile_hash", sa.String(length=64)))
    # Historical builds keep their hashes, manifests, and active pointers. The
    # label only makes their lack of a pinned code profile explicit to readers.
    op.execute(
        sa.text(
            "UPDATE index_builds SET index_profile_id = 'legacy-unprofiled' "
            "WHERE index_profile_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("index_builds", "index_profile_hash")
    op.drop_column("index_builds", "index_profile_id")
