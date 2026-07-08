"""Unit tests for OrganizationService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.organization import Organization
from app.modules.organizations.schemas.organization import OrganizationCreate, OrganizationUpdate
from app.modules.organizations.services.organization_service import OrganizationService
from app.platform.auth.events import OrganizationAuthInvalidated
from app.platform.http.pagination import ListParams

pytestmark = pytest.mark.unit


def _organization(*, name: str = "Acme", is_active: bool = True) -> Organization:
    return Organization(
        id=uuid.uuid4(),
        name=name,
        description=None,
        is_active=is_active,
        deleted_at=None,
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
def auth_events() -> AsyncMock:
    mock = AsyncMock()
    mock.publish = AsyncMock()
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    repository: AsyncMock,
    auth_events: AsyncMock,
) -> OrganizationService:
    return OrganizationService(session=session, repository=repository, auth_events=auth_events)


async def test_create_persists_organization(
    service: OrganizationService, session: AsyncMock, repository: AsyncMock
) -> None:
    repository.flush = AsyncMock()
    result = await service.create(OrganizationCreate(name="Acme", description="desc"))
    assert result.name == "Acme"
    repository.add.assert_called_once()
    session.commit.assert_awaited_once()


async def test_get_raises_not_found(service: OrganizationService, repository: AsyncMock) -> None:
    repository.get_by_id.return_value = None
    with pytest.raises(NotFoundError) as exc_info:
        await service.get(uuid.uuid4())
    assert exc_info.value.code == "organization_not_found"


async def test_update_empty_body_raises_bad_request(service: OrganizationService) -> None:
    with pytest.raises(BadRequestError) as exc_info:
        await service.update(uuid.uuid4(), OrganizationUpdate())
    assert exc_info.value.code == "empty_update"


async def test_list_returns_paginated_result(
    service: OrganizationService, repository: AsyncMock
) -> None:
    items = [_organization(name="A"), _organization(name="B")]
    repository.list_page.return_value = items
    repository.count.return_value = 2
    result = await service.list(ListParams(limit=10, offset=0))
    assert result.total == 2
    assert len(result.items) == 2


async def test_update_does_not_publish_auth_invalidation(
    service: OrganizationService,
    repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org = _organization()
    repository.get_by_id.return_value = org
    repository.flush = AsyncMock()

    await service.update(org.id, OrganizationUpdate(name="Renamed"))

    auth_events.publish.assert_not_awaited()


async def test_toggle_status_publishes_auth_invalidation(
    service: OrganizationService,
    session: AsyncMock,
    repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org = _organization()
    repository.get_by_id.return_value = org
    repository.flush = AsyncMock()

    result = await service.toggle_status(org.id)

    assert result.is_active is False
    auth_events.publish.assert_awaited_once_with(OrganizationAuthInvalidated(org.id))


async def test_soft_delete_publishes_auth_invalidation(
    service: OrganizationService,
    session: AsyncMock,
    repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org = _organization()
    repository.get_by_id.return_value = org
    repository.flush = AsyncMock()

    await service.soft_delete(org.id)

    auth_events.publish.assert_awaited_once_with(OrganizationAuthInvalidated(org.id))
