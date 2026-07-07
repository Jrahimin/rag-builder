"""add document extraction metadata columns

Revision ID: 0013_doc_extraction_meta
Revises: 0012_document_ocr_lang
Create Date: 2026-07-07 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_doc_extraction_meta"
down_revision: str | None = "0012_document_ocr_lang"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("accepted_parser", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("parse_quality_score", sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("extraction_method", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "extraction_method")
    op.drop_column("documents", "parse_quality_score")
    op.drop_column("documents", "accepted_parser")
