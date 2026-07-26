"""Contextual generation traces.

Revision ID: 0021_contextual_generation
Revises: 0020_hosted_webhooks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_contextual_generation"
down_revision: str | None = "0020_hosted_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generations",
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="processing", nullable=False),
        sa.Column(
            "grounding_status",
            sa.String(32),
            server_default="context_supplied",
            nullable=False,
        ),
        sa.Column("grounded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("response_schema", postgresql.JSONB(), nullable=False),
        sa.Column("locale", sa.String(35), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=True),
        sa.Column(
            "generation_config",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("retention_mode", sa.String(32), nullable=False),
        sa.Column("retained_input", postgresql.JSONB(), nullable=True),
        sa.Column("retained_context", postgresql.JSONB(), nullable=True),
        sa.Column(
            "payload_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("finish_reason", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("provider_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generations")),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key_hash",
            name="uq_generations_project_idempotency",
        ),
    )
    op.create_index(
        "ix_generations_project_created",
        "generations",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generations_project_status",
        "generations",
        ["project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("generations")
