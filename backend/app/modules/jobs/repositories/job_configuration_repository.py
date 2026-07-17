"""Persistence for immutable, content-addressed job configuration."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_configuration_snapshot import JobConfigurationSnapshot
from app.platform.jobs.contracts import JobConfiguration


class JobConfigurationRepository:
    """Project-scoped snapshot access with race-safe deduplication."""

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    async def get(self, snapshot_id: uuid.UUID) -> JobConfigurationSnapshot | None:
        result = await self._session.execute(
            select(JobConfigurationSnapshot).where(
                JobConfigurationSnapshot.id == snapshot_id,
                JobConfigurationSnapshot.project_id == self._project_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        configuration: JobConfiguration,
    ) -> JobConfigurationSnapshot:
        configuration_hash = configuration.digest()
        snapshot_id = uuid.uuid4()
        stmt = (
            insert(JobConfigurationSnapshot)
            .values(
                id=snapshot_id,
                project_id=self._project_id,
                schema_version=configuration.schema_version,
                configuration_hash=configuration_hash,
                configuration=configuration.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(
                index_elements=["project_id", "configuration_hash"],
            )
            .returning(JobConfigurationSnapshot.id)
        )
        inserted_id = (await self._session.execute(stmt)).scalar_one_or_none()
        target_id = inserted_id
        if target_id is None:
            result = await self._session.execute(
                select(JobConfigurationSnapshot.id).where(
                    JobConfigurationSnapshot.project_id == self._project_id,
                    JobConfigurationSnapshot.configuration_hash == configuration_hash,
                )
            )
            target_id = result.scalar_one()
        snapshot = await self.get(target_id)
        if snapshot is None:  # pragma: no cover - database invariant
            msg = "Configuration snapshot upsert did not produce a row"
            raise RuntimeError(msg)
        return snapshot
