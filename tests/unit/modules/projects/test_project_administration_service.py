"""Phase 1 Project policy revision and ownership-lock tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.exceptions import ConflictError
from app.models.project import Project
from app.modules.projects.services.project_ai_config_service import (
    ProjectAdministrationService,
)
from app.platform.config.project_ai import ProjectAIConfig
from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID

pytestmark = pytest.mark.unit


def _project(*, locked: bool, organization_id: uuid.UUID = DEFAULT_ORGANIZATION_ID) -> Project:
    return Project(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Legacy",
        description=None,
        is_active=True,
        ownership_locked=locked,
        active_ai_config_revision_id=None,
        source_metadata_generation=0,
        deleted_at=None,
        deleted_by=None,
    )


def _service(
    session: AsyncMock,
    repository: AsyncMock,
    audit: MagicMock,
    project_id: uuid.UUID,
) -> ProjectAdministrationService:
    return ProjectAdministrationService(
        session=session,
        project_id=project_id,
        repository=repository,
        settings=Settings(),
        audit=audit,
        actor_id="operator-1",
    )


async def test_config_revision_is_append_only_and_activates_pointer() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.add = MagicMock()
    repository.next_revision_number.return_value = 1
    project = _project(locked=True)
    repository.lock_project.return_value = project
    audit = MagicMock()
    service = _service(session, repository, audit, project.id)

    revision = await service.create_revision(
        ProjectAIConfig.model_validate({"llm": {"model": "policy-model"}}),
        expected_active_revision_id=None,
        reason="Initial policy",
    )

    assert revision.revision_number == 1
    assert revision.configuration["llm"]["model"] == "policy-model"
    assert project.active_ai_config_revision_id == revision.id
    repository.add.assert_called_once_with(revision)
    session.commit.assert_awaited_once()
    audit.record.assert_called_once()


async def test_config_revision_uses_optimistic_concurrency() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    active_id = uuid.uuid4()
    project = _project(locked=True)
    project.active_ai_config_revision_id = active_id
    repository.lock_project.return_value = project
    service = _service(session, repository, MagicMock(), project.id)

    with pytest.raises(ConflictError) as caught:
        await service.create_revision(
            ProjectAIConfig(),
            expected_active_revision_id=uuid.uuid4(),
            reason="Stale write",
        )

    assert caught.value.code == "project_config_revision_conflict"
    repository.add.assert_not_called()


async def test_locked_project_cannot_be_reassigned() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    project = _project(locked=True)
    repository.lock_project.return_value = project
    service = _service(session, repository, MagicMock(), project.id)

    with pytest.raises(ConflictError) as caught:
        await service.reassign_ownership(
            expected_current_organization_id=project.organization_id,
            target_organization_id=uuid.uuid4(),
            reason="Move",
        )

    assert caught.value.code == "project_ownership_locked"


async def test_legacy_confirmation_locks_ownership_without_changing_id() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    project = _project(locked=False)
    repository.lock_project.return_value = project
    audit = MagicMock()
    service = _service(session, repository, audit, project.id)

    result = await service.confirm_ownership(
        expected_current_organization_id=DEFAULT_ORGANIZATION_ID,
        reason="Default Organization is correct",
    )

    assert result.id == project.id
    assert result.organization_id == DEFAULT_ORGANIZATION_ID
    assert result.ownership_locked is True
    session.commit.assert_awaited_once()
    detail = audit.record.call_args.kwargs["detail"]
    assert detail["previous_organization_id"] == str(DEFAULT_ORGANIZATION_ID)
    assert detail["target_organization_id"] == str(DEFAULT_ORGANIZATION_ID)
