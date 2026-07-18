"""Atomic activation and verified rollback guard coverage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import BadRequestError
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.modules.retrieval.services.index_lifecycle_service import IndexLifecycleService
from app.platform.jobs.configuration import build_job_configuration

pytestmark = pytest.mark.unit


def _service() -> IndexLifecycleService:
    project_id = uuid.uuid4()
    service = IndexLifecycleService(
        session=AsyncMock(),
        project_id=project_id,
        job_submitter=MagicMock(),
        job_configuration=build_job_configuration(Settings()),
        embedding_set_version=1,
        job_max_attempts=3,
        audit=MagicMock(),
    )
    service._repository = MagicMock()
    return service


def _build(state: IndexBuildState, *, validated: bool = True) -> IndexBuild:
    now = datetime.now(UTC)
    return IndexBuild(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        operation=IndexBuildOperation.REINDEX,
        state=state,
        embedding_set_version=1,
        configuration_hash="a" * 64,
        corpus_fingerprint="b" * 64 if validated else None,
        validated_at=now if validated else None,
        created_at=now,
        updated_at=now,
    )


async def test_failed_or_partial_build_cannot_activate() -> None:
    service = _service()
    service._repository.get_by_id = AsyncMock(return_value=_build(IndexBuildState.FAILED))
    with pytest.raises(BadRequestError) as exc_info:
        await service.activate(uuid.uuid4())
    assert exc_info.value.code == "index_build_not_activatable"


async def test_verified_build_activation_uses_single_pointer_transition() -> None:
    service = _service()
    build = _build(IndexBuildState.VALIDATED)
    service._repository.get_by_id = AsyncMock(return_value=build)
    with patch(
        "app.modules.retrieval.services.index_lifecycle_service.activate_index_build",
        new_callable=AsyncMock,
    ) as activate:
        result = await service.activate(build.id)
    assert result is build
    activate.assert_awaited_once_with(service._session, service._project_id, build)
    service._session.commit.assert_awaited_once()
