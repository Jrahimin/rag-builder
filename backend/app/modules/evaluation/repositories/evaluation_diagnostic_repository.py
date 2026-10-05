"""Project-scoped capture with strict write/read expiry and bounded allowlisted content."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation_diagnostic import EvaluationDiagnostic

MAX_PAYLOAD_BYTES = 262144
TTL = timedelta(days=7)
ALLOWED = {
    "intent",
    "requirements",
    "evidence",
    "verified_assertions",
    "limitations",
    "rejected_attempts",
    "terminal",
    "fatal_event",
    "recovery_stop",
    "correction_attempts",
    "calculations",
}


def bounded_capture(value: dict[str, Any]) -> dict[str, Any]:
    result = {k: v for k, v in value.items() if k in ALLOWED}
    encoded = json.dumps(result, ensure_ascii=False, default=str).encode()
    if len(encoded) > MAX_PAYLOAD_BYTES:
        return {
            "truncated": True,
            "payload_hash": hashlib.sha256(encoded).hexdigest(),
            "original_bytes": len(encoded),
        }
    return result


class EvaluationDiagnosticRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID):
        self.session = session
        self.project_id = project_id

    async def expire_payloads(self, now: datetime | None = None) -> None:
        await self.session.execute(
            update(EvaluationDiagnostic)
            .where(
                EvaluationDiagnostic.project_id == self.project_id,
                EvaluationDiagnostic.expires_at <= (now or datetime.now(UTC)),
                EvaluationDiagnostic.payload.is_not(None),
            )
            .values(payload=None)
        )

    async def capture(
        self, run_id: uuid.UUID, case_key: str, profile: str, payload: dict[str, Any]
    ) -> None:
        await self.expire_payloads()
        self.session.add(
            EvaluationDiagnostic(
                project_id=self.project_id,
                run_id=run_id,
                case_key=case_key,
                profile=profile,
                payload=bounded_capture(payload),
                expires_at=datetime.now(UTC) + TTL,
            )
        )

    async def get(self, run_id: uuid.UUID) -> list[EvaluationDiagnostic]:
        await self.expire_payloads()
        rows = await self.session.execute(
            select(EvaluationDiagnostic).where(
                EvaluationDiagnostic.project_id == self.project_id,
                EvaluationDiagnostic.run_id == run_id,
            )
        )
        return list(rows.scalars())
