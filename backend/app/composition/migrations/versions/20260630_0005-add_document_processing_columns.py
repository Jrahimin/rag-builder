"""add document processing columns

Revision ID: 0005_document_processing
Revises: 0004_add_documents
Create Date: 2026-06-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_document_processing"
down_revision: str | None = "0004_add_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("parser_name", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("parser_version", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("language", sa.String(length=16), nullable=True))
    op.add_column(
        "documents",
        sa.Column("parsed_text_storage_key", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "parsed_text_storage_key")
    op.drop_column("documents", "language")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "parser_version")
    op.drop_column("documents", "parser_name")
    op.drop_column("documents", "error_message")
