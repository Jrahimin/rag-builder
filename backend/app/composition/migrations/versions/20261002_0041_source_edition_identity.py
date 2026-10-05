"""Declare legal edition identity separately from administrative corrections."""

import sqlalchemy as sa
from alembic import op

revision = "20261002_0041"
down_revision = "20261002_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_metadata_revisions", sa.Column("edition_key", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source_metadata_revisions", "edition_key")
