"""add keyword index tables for hybrid retrieval

Revision ID: 0009_keyword_index
Revises: 0008_conversations
Create Date: 2026-07-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_keyword_index"
down_revision: str | None = "0008_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_keyword_index",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_set_version", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("content_normalized", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("term_frequencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metadata_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
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
            name=op.f("fk_chunk_keyword_index_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunk_keyword_index_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_chunk_keyword_index_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_keyword_index")),
        sa.UniqueConstraint(
            "chunk_id",
            "embedding_set_version",
            name=op.f("uq_chunk_keyword_index_chunk_esv"),
        ),
    )
    op.create_index(
        "ix_chunk_keyword_index_project_esv",
        "chunk_keyword_index",
        ["project_id", "embedding_set_version"],
        unique=False,
    )
    op.create_index(
        "ix_chunk_keyword_index_search_vector",
        "chunk_keyword_index",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_chunk_keyword_index_document_id"),
        "chunk_keyword_index",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chunk_keyword_index_chunk_id"),
        "chunk_keyword_index",
        ["chunk_id"],
        unique=False,
    )

    op.create_table(
        "keyword_term_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_set_version", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(length=128), nullable=False),
        sa.Column("document_frequency", sa.Integer(), nullable=False),
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
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_keyword_term_stats_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keyword_term_stats")),
        sa.UniqueConstraint(
            "project_id",
            "embedding_set_version",
            "term",
            name=op.f("uq_keyword_term_stats_project_esv_term"),
        ),
    )
    op.create_index(
        "ix_keyword_term_stats_project_esv",
        "keyword_term_stats",
        ["project_id", "embedding_set_version"],
        unique=False,
    )

    op.create_table(
        "keyword_collection_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_set_version", sa.Integer(), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_doc_length", sa.Float(), nullable=False, server_default="0"),
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
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_keyword_collection_stats_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keyword_collection_stats")),
        sa.UniqueConstraint(
            "project_id",
            "embedding_set_version",
            name=op.f("uq_keyword_collection_stats_project_esv"),
        ),
    )


def downgrade() -> None:
    op.drop_table("keyword_collection_stats")
    op.drop_index("ix_keyword_term_stats_project_esv", table_name="keyword_term_stats")
    op.drop_table("keyword_term_stats")
    op.drop_index(op.f("ix_chunk_keyword_index_chunk_id"), table_name="chunk_keyword_index")
    op.drop_index(op.f("ix_chunk_keyword_index_document_id"), table_name="chunk_keyword_index")
    op.drop_index("ix_chunk_keyword_index_search_vector", table_name="chunk_keyword_index")
    op.drop_index("ix_chunk_keyword_index_project_esv", table_name="chunk_keyword_index")
    op.drop_table("chunk_keyword_index")
