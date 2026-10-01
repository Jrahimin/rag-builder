"""Provider-independent inspection of project corpus snapshots."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.schemas.index_lifecycle import IndexBuildListResponse, IndexBuildResponse


class IndexBuildReadService:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self.repository = IndexBuildRepository(session, project_id)

    async def list(self) -> IndexBuildListResponse:
        pointer = await self.repository.get_pointer()
        builds = await self.repository.list_recent()
        return IndexBuildListResponse(
            items=[IndexBuildResponse.model_validate(item) for item in builds],
            active_build_id=pointer.active_build_id if pointer else None,
            previous_build_id=pointer.previous_build_id if pointer else None,
        )
