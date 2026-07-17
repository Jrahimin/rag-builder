"""Unit tests for ProjectService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.project import Project
from app.modules.projects.schemas.project import ProjectCreate, ProjectUpdate
from app.modules.projects.services.project_service import ProjectService
from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID
from app.platform.http.pagination import ListParams

pytestmark = pytest.mark.unit


def _project(
    *,
    name: str = "Test",
    deleted_at: datetime | None = None,
    is_active: bool = True,
) -> Project:
    return Project(
        id=uuid.uuid4(),
        organization_id=DEFAULT_ORGANIZATION_ID,
        name=name,
        description=None,
        is_active=is_active,
        deleted_at=deleted_at,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    return mock


@pytest.fixture
def service(session: AsyncMock, repository: AsyncMock) -> ProjectService:
    return ProjectService(session=session, repository=repository)


async def test_create_persists_and_commits(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    repository.exists_by_name.return_value = False
    repository.flush = AsyncMock()

    result = await service.create(ProjectCreate(name="Alpha", description="desc"))

    assert result.name == "Alpha"
    assert result.is_active is True
    repository.add.assert_called_once()
    repository.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_create_duplicate_name_raises_conflict(
    service: ProjectService, repository: AsyncMock
) -> None:
    repository.exists_by_name.return_value = True

    with pytest.raises(ConflictError) as exc_info:
        await service.create(ProjectCreate(name="Alpha"))

    assert exc_info.value.code == "project_name_conflict"


async def test_create_integrity_error_rolls_back(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    repository.exists_by_name.return_value = False
    repository.flush = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))

    with pytest.raises(ConflictError):
        await service.create(ProjectCreate(name="Alpha"))

    session.rollback.assert_awaited_once()


async def test_get_raises_not_found(service: ProjectService, repository: AsyncMock) -> None:
    repository.get_by_id_for_organization.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        await service.get(uuid.uuid4())

    assert exc_info.value.code == "project_not_found"


async def test_get_deleted_treated_as_not_found(
    service: ProjectService, repository: AsyncMock
) -> None:
    deleted = _project(deleted_at=datetime.now(UTC))

    repository.get_by_id_for_organization.return_value = None

    with pytest.raises(NotFoundError):
        await service.get(deleted.id)


async def test_list_returns_paginated_result(
    service: ProjectService, repository: AsyncMock
) -> None:
    items = [_project(name="A"), _project(name="B")]
    repository.list_page.return_value = items
    repository.count.return_value = 2

    result = await service.list(ListParams(limit=10, offset=0))

    assert result.total == 2
    assert len(result.items) == 2
    assert result.limit == 10


async def test_update_deleted_project_raises_conflict(
    service: ProjectService, repository: AsyncMock
) -> None:
    deleted = _project(deleted_at=datetime.now(UTC))

    repository.get_by_id_for_organization.return_value = deleted

    with pytest.raises(ConflictError) as exc_info:
        await service.update(deleted.id, ProjectUpdate(name="New"))

    assert exc_info.value.code == "project_deleted"


async def test_update_empty_body_raises_bad_request(
    service: ProjectService, repository: AsyncMock
) -> None:
    with pytest.raises(BadRequestError) as exc_info:
        await service.update(uuid.uuid4(), ProjectUpdate())

    assert exc_info.value.code == "empty_update"
    repository.get_by_id_for_organization.assert_not_awaited()


async def test_update_duplicate_name_raises_conflict(
    service: ProjectService, repository: AsyncMock
) -> None:
    project = _project(name="Old")
    repository.get_by_id_for_organization.return_value = project
    repository.exists_by_name.return_value = True

    with pytest.raises(ConflictError) as exc_info:
        await service.update(project.id, ProjectUpdate(name="Taken"))

    assert exc_info.value.code == "project_name_conflict"


async def test_toggle_status_on_deleted_raises_conflict(
    service: ProjectService, repository: AsyncMock
) -> None:
    deleted = _project(deleted_at=datetime.now(UTC))
    repository.get_by_id_for_organization.return_value = deleted

    with pytest.raises(ConflictError) as exc_info:
        await service.toggle_status(deleted.id)

    assert exc_info.value.code == "project_deleted"


async def test_toggle_status_flips_true_to_false(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    project = _project(is_active=True)
    repository.get_by_id_for_organization.return_value = project
    repository.flush = AsyncMock()

    result = await service.toggle_status(project.id)

    assert result.is_active is False
    repository.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_toggle_status_flips_false_to_true(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    project = _project(is_active=False)
    repository.get_by_id_for_organization.return_value = project
    repository.flush = AsyncMock()

    result = await service.toggle_status(project.id)

    assert result.is_active is True
    repository.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_soft_delete_sets_fields_and_commits(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    project = _project(is_active=True)
    repository.get_by_id_for_organization.return_value = project
    repository.flush = AsyncMock()

    result = await service.soft_delete(project.id)

    assert result.deleted_at is not None
    assert result.deleted_by is None
    assert result.is_active is False
    session.commit.assert_awaited_once()


async def test_soft_delete_idempotent_skips_commit(
    service: ProjectService, session: AsyncMock, repository: AsyncMock
) -> None:
    deleted = _project(deleted_at=datetime.now(UTC), is_active=False)

    async def _get_for_org(
        project_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        *,
        include_deleted: bool = False,
    ) -> Project | None:
        if include_deleted:
            return deleted
        return None

    repository.get_by_id_for_organization.side_effect = _get_for_org

    result = await service.soft_delete(deleted.id)

    assert result.deleted_at is not None
    session.commit.assert_not_awaited()
