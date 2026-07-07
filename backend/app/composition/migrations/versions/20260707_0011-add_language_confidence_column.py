"""add language_confidence to documents

Revision ID: 0011_language_confidence
Revises: 0010_chunk_page_range
Create Date: 2026-07-07 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_language_confidence"
down_revision: str | None = "0010_chunk_page_range"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("language_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "language_confidence")
