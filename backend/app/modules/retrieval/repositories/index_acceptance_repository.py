"""Project-scoped persistence for immutable definitions and quality receipts."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.index_acceptance import IndexAcceptance, IndexAcceptanceReport, IndexAcceptanceSet


class IndexAcceptanceRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID):
        self.session = session
        self.project_id = project_id

    async def list(self, build_id: uuid.UUID) -> list[IndexAcceptance]:
        rows = await self.session.execute(
            select(IndexAcceptance)
            .where(
                IndexAcceptance.project_id == self.project_id, IndexAcceptance.build_id == build_id
            )
            .order_by(IndexAcceptance.created_at.desc())
        )
        return list(rows.scalars().all())

    async def by_hash(self, artifact_hash: str) -> IndexAcceptance | None:
        return await self.session.scalar(
            select(IndexAcceptance).where(
                IndexAcceptance.project_id == self.project_id,
                IndexAcceptance.artifact_hash == artifact_hash,
            )
        )

    async def definition(self, build_id: uuid.UUID) -> IndexAcceptanceSet | None:
        return await self.session.scalar(
            select(IndexAcceptanceSet).where(
                IndexAcceptanceSet.project_id == self.project_id,
                IndexAcceptanceSet.build_id == build_id,
            )
        )

    async def observed_report(
        self, report_id: uuid.UUID | None, build_id: uuid.UUID
    ) -> dict[str, Any] | None:
        if report_id is None:
            return None
        row = await self.session.scalar(
            select(IndexAcceptanceReport).where(
                IndexAcceptanceReport.id == report_id,
                IndexAcceptanceReport.project_id == self.project_id,
                IndexAcceptanceReport.build_id == build_id,
            )
        )
        if row is None:
            return None
        import hashlib
        import json

        actual = hashlib.sha256(
            json.dumps(
                row.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        return row.report if actual == row.report_hash else None

    def add(self, row: IndexAcceptance | IndexAcceptanceSet) -> None:
        if row.project_id != self.project_id:
            raise ValueError("Acceptance project differs from repository scope")
        self.session.add(row)
