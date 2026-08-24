"""Persistence access for Super Admin accounts and sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_session import AdminSession
from app.models.admin_user import AdminUser


class AdminAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_admin_by_email(self, email: str) -> AdminUser | None:
        return await self._session.scalar(
            select(AdminUser).where(AdminUser.email == email, AdminUser.deleted_at.is_(None))
        )

    async def get_admin(self, admin_id: uuid.UUID) -> AdminUser | None:
        return await self._session.get(AdminUser, admin_id)

    async def get_active_session(self, session_id: uuid.UUID) -> AdminSession | None:
        return await self._session.scalar(
            select(AdminSession).where(
                AdminSession.id == session_id,
                AdminSession.revoked_at.is_(None),
                AdminSession.expires_at > datetime.now(UTC),
            )
        )

    async def get_session_by_refresh_hash(self, token_hash: str) -> AdminSession | None:
        return await self._session.scalar(
            select(AdminSession).where(
                AdminSession.refresh_token_hash == token_hash,
                AdminSession.revoked_at.is_(None),
                AdminSession.expires_at > datetime.now(UTC),
            )
        )

    async def revoke_sessions_for_admin(self, admin_id: uuid.UUID) -> None:
        await self._session.execute(
            update(AdminSession)
            .where(AdminSession.admin_user_id == admin_id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    def add(self, entity: AdminUser | AdminSession) -> None:
        self._session.add(entity)

    async def commit(self) -> None:
        await self._session.commit()
