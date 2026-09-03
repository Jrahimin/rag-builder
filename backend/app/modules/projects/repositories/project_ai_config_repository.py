"""Persistence for immutable Project AI configuration revisions."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.project_ai_config_revision import ProjectAIConfigRevision


class ProjectAIConfigRepository:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    async def lock_project(self) -> Project | None:
        result = await self._session.execute(
            select(Project)
            .where(Project.id == self._project_id, Project.deleted_at.is_(None))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> ProjectAIConfigRevision | None:
        result = await self._session.execute(
            select(ProjectAIConfigRevision)
            .join(
                Project,
                (Project.active_ai_config_revision_id == ProjectAIConfigRevision.id)
                & (Project.id == ProjectAIConfigRevision.project_id),
            )
            .where(
                Project.id == self._project_id,
                ProjectAIConfigRevision.project_id == self._project_id,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, revision_id: uuid.UUID) -> ProjectAIConfigRevision | None:
        result = await self._session.execute(
            select(ProjectAIConfigRevision).where(
                ProjectAIConfigRevision.id == revision_id,
                ProjectAIConfigRevision.project_id == self._project_id,
            )
        )
        return result.scalar_one_or_none()

    async def next_revision_number(self) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(ProjectAIConfigRevision.revision_number), 0)).where(
                ProjectAIConfigRevision.project_id == self._project_id
            )
        )
        return int(value or 0) + 1

    async def list(self, *, limit: int, offset: int) -> list[ProjectAIConfigRevision]:
        result = await self._session.execute(
            select(ProjectAIConfigRevision)
            .where(
                ProjectAIConfigRevision.project_id == self._project_id,
                ProjectAIConfigRevision.schema_version == 2,
            )
            .order_by(ProjectAIConfigRevision.revision_number.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    def add(self, revision: ProjectAIConfigRevision) -> None:
        self._session.add(revision)
