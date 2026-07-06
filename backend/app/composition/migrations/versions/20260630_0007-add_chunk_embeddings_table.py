"""add chunk_embeddings table and embedded document status

Revision ID: 0007_chunk_embeddings
Revises: 0006_add_document_chunks
Create Date: 2026-06-30 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_chunk_embeddings"
down_revision: str | None = "0006_add_document_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_set_version", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("provider_version", sa.String(length=64), nullable=False),
        sa.Column("input_content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_schema_version", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunk_embeddings_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_chunk_embeddings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint(
            "chunk_id",
            "embedding_set_version",
            "provider",
            "model",
            name=op.f("uq_chunk_embeddings_chunk_esv_provider_model"),
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_project_document",
        "chunk_embeddings",
        ["project_id", "document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chunk_embeddings_document_id"),
        "chunk_embeddings",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chunk_embeddings_chunk_id"),
        "chunk_embeddings",
        ["chunk_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chunk_embeddings_chunk_id"), table_name="chunk_embeddings")
    op.drop_index(op.f("ix_chunk_embeddings_document_id"), table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_project_document", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
