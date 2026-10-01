"""Fence versioned private structural builds, including jobs staged before the fence."""
from alembic import op
import sqlalchemy as sa
revision = "20260930_0036"
down_revision = "20260930_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("index_builds", sa.Column("structural_contract_version", sa.String(32), nullable=True))
    op.execute("""UPDATE index_builds AS b SET structural_contract_version='structure.v1'
                  FROM job_runs AS j WHERE b.job_id=j.id AND j.payload->>'structural_reprocess'='true'""")


def downgrade() -> None:
    op.drop_column("index_builds", "structural_contract_version")
