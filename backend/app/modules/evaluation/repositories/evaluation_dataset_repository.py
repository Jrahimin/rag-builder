"""Project-scoped evaluation dataset persistence."""

from __future__ import annotations

from sqlalchemy import select

from app.models.evaluation_dataset import EvaluationDataset
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class EvaluationDatasetRepository(ProjectScopedRepository[EvaluationDataset]):
    model = EvaluationDataset

    async def get_by_name_version(self, name: str, version: str) -> EvaluationDataset | None:
        result = await self._session.execute(
            self._scoped().where(
                EvaluationDataset.name == name,
                EvaluationDataset.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def list_latest(self, *, limit: int, offset: int) -> list[EvaluationDataset]:
        result = await self._session.execute(
            self._scoped()
            .order_by(EvaluationDataset.created_at.desc(), EvaluationDataset.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def latest(self) -> EvaluationDataset | None:
        return await self._session.scalar(
            select(EvaluationDataset)
            .where(EvaluationDataset.project_id == self._project_id)
            .order_by(EvaluationDataset.created_at.desc(), EvaluationDataset.id.desc())
            .limit(1)
        )
