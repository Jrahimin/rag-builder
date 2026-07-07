"""add page range columns to document_chunks

Revision ID: 0010_chunk_page_range
Revises: 0009_keyword_index
Create Date: 2026-07-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_chunk_page_range"
down_revision: str | None = "0009_keyword_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("document_chunks", sa.Column("page_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "page_end")
    op.drop_column("document_chunks", "page_start")
