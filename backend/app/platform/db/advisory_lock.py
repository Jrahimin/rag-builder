"""PostgreSQL transaction-scoped locks for replay-safe document stages."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def document_stage_lock_key(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    stage: str,
) -> int:
    payload = f"{project_id}:{document_id}:{stage}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def project_stage_lock_key(project_id: uuid.UUID, stage: str) -> int:
    payload = f"{project_id}:{stage}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_document_stage_lock(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    stage: str,
) -> None:
    """Fence duplicate/stale workers until the stage transaction completes."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": document_stage_lock_key(project_id, document_id, stage)},
    )


async def acquire_project_stage_lock(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    stage: str,
) -> None:
    """Serialize one Project-wide metadata transition for this transaction."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": project_stage_lock_key(project_id, stage)},
    )
