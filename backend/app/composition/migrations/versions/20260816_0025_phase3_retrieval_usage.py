"""Phase 3 retrieval provenance and normalized usage fields.

Revision ID: 0025_phase3_usage
Revises: 0024_phase2_sources
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_phase3_usage"
down_revision: str | None = "0024_phase2_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, message_column_type in (
        ("index_build_id", sa.Uuid()),
        ("source_metadata_generation", sa.Integer()),
        ("retrieval_latency_ms", sa.Integer()),
        ("provider_latency_ms", sa.Integer()),
        ("total_latency_ms", sa.Integer()),
    ):
        op.add_column("messages", sa.Column(name, message_column_type, nullable=True))
    op.create_index("ix_messages_index_build_id", "messages", ["index_build_id"])
    op.create_index(
        "ix_messages_project_usage_created",
        "messages",
        ["project_id", "role", "created_at"],
    )
    op.execute(
        """
        UPDATE messages
        SET index_build_id = NULLIF(metadata->>'index_build_id', '')::uuid,
            source_metadata_generation =
                NULLIF(metadata->>'source_metadata_generation', '')::integer,
            retrieval_latency_ms = COALESCE(
                NULLIF(metadata->>'retrieval_time_ms', '')::integer,
                NULLIF(metadata->>'retrieval_ms', '')::integer
            ),
            provider_latency_ms = COALESCE(
                NULLIF(metadata->>'generation_time_ms', '')::integer,
                NULLIF(metadata->>'generation_ms', '')::integer
            ),
            total_latency_ms = COALESCE(
                NULLIF(metadata->>'total_time_ms', '')::integer,
                NULLIF(metadata->>'total_ms', '')::integer
            )
        WHERE role = 'assistant'
        """
    )

    op.add_column("generations", sa.Column("index_build_id", sa.Uuid(), nullable=True))
    op.add_column(
        "generations", sa.Column("source_metadata_generation", sa.Integer(), nullable=True)
    )
    op.create_index("ix_generations_index_build_id", "generations", ["index_build_id"])
    op.execute(
        """
        UPDATE generations
        SET index_build_id = NULLIF(config_provenance->>'active_index_build_id', '')::uuid,
            source_metadata_generation =
                NULLIF(config_provenance->>'source_metadata_generation', '')::integer
        """
    )

    for name, evaluation_column_type in (
        ("provider", sa.String(length=64)),
        ("model", sa.String(length=128)),
        ("input_tokens", sa.Integer()),
        ("output_tokens", sa.Integer()),
        ("retrieval_latency_ms", sa.Integer()),
        ("provider_latency_ms", sa.Integer()),
        ("total_latency_ms", sa.Integer()),
        ("index_build_id", sa.Uuid()),
        ("source_metadata_generation", sa.Integer()),
    ):
        op.add_column(
            "evaluation_runs", sa.Column(name, evaluation_column_type, nullable=True)
        )
    op.create_index(
        "ix_evaluation_runs_index_build_id", "evaluation_runs", ["index_build_id"]
    )
    op.execute(
        """
        UPDATE evaluation_runs
        SET provider = config_snapshot->'configuration'->'llm'->>'provider',
            model = config_snapshot->'configuration'->'llm'->>'model',
            index_build_id = NULLIF(config_provenance->>'active_index_build_id', '')::uuid,
            source_metadata_generation =
                NULLIF(config_provenance->>'source_metadata_generation', '')::integer
        """
    )


def downgrade() -> None:
    raise RuntimeError("Phase 3 provenance/usage migration is intentionally irreversible")
