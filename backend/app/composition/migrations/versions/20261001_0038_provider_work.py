"""Durable embedding reuse and independent provider billing attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261001_0038"
down_revision = "20261001_0037"
branch_labels = None
depends_on = None


def common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        *common_columns(),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("identity", postgresql.JSONB(), nullable=False),
        sa.Column("vector", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "cache_key", name="uq_embedding_cache_project_key"),
    )
    op.create_index("ix_embedding_cache_expiry", "embedding_cache", ["expires_at"])
    op.create_table(
        "provider_usage_attempts",
        *common_columns(),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("workload", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("price_version", sa.String(64), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("billed_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("billed_tokens", sa.BigInteger(), nullable=True),
        sa.Column("search_units", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_provider_usage_project_created", "provider_usage_attempts", ["project_id", "created_at"]
    )
    op.create_index(
        "ix_provider_usage_reference", "provider_usage_attempts", ["project_id", "reference"]
    )


def downgrade() -> None:
    op.drop_table("provider_usage_attempts")
    op.drop_table("embedding_cache")
