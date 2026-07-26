"""Project-scoped persistence for contextual generation traces."""

from __future__ import annotations

from app.models.generation import Generation
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class GenerationRepository(ProjectScopedRepository[Generation]):
    """Fail-closed generation trace repository."""

    model = Generation

    async def get_by_idempotency_key_hash(self, key_hash: str) -> Generation | None:
        result = await self._session.execute(
            self._scoped().where(Generation.idempotency_key_hash == key_hash)
        )
        return result.scalar_one_or_none()
