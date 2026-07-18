"""Project-scoped immutable index build and pointer persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.index_build import IndexBuild, IndexBuildState, ProjectIndexPointer
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class IndexBuildRepository(ProjectScopedRepository[IndexBuild]):
    model = IndexBuild

    async def get_by_job_id(self, job_id: uuid.UUID) -> IndexBuild | None:
        result = await self._session.execute(self._scoped().where(self.model.job_id == job_id))
        return result.scalar_one_or_none()

    async def list_recent(self, *, limit: int = 50) -> list[IndexBuild]:
        result = await self._session.execute(
            self._scoped().order_by(self.model.created_at.desc(), self.model.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[IndexBuild]:
        result = await self._session.execute(
            self._scoped().order_by(self.model.created_at.desc(), self.model.id.desc())
        )
        return list(result.scalars().all())

    async def get_pointer(self, *, for_update: bool = False) -> ProjectIndexPointer | None:
        stmt = select(ProjectIndexPointer).where(ProjectIndexPointer.project_id == self._project_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active(self) -> IndexBuild | None:
        pointer = await self.get_pointer()
        if pointer is None or pointer.active_build_id is None:
            return None
        build = await self.get_by_id(pointer.active_build_id)
        if build is None or build.state is not IndexBuildState.ACTIVE or build.validated_at is None:
            return None
        return build

    def add_pointer(self, pointer: ProjectIndexPointer) -> None:
        if pointer.project_id != self._project_id:
            raise ValueError("Index pointer project_id does not match repository scope")
        self._session.add(pointer)
