"""Optional immutable work identity for translations and reprints.

Revision ID: 20260908_0033
Revises: 20260903_0032
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0033"
down_revision = "0032_rag_phase2_grounding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_metadata_revisions", sa.Column("work_key", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("source_metadata_revisions", "work_key")
