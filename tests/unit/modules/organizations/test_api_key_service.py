"""Unit tests for ApiKeyService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.models.organization import Organization
from app.models.organization_api_key import OrganizationApiKey
from app.modules.organizations.schemas.api_key import ApiKeyCreate
from app.modules.organizations.services.api_key_service import ApiKeyService, _rotation_name
from app.platform.auth.events import ApiKeyAuthInvalidated
from app.platform.domain.api_key_crypto import hash_key, verify_key

pytestmark = pytest.mark.unit

PEPPER = "test-pepper-at-least-32-characters-long"


def _organization() -> Organization:
    return Organization(
        id=uuid.uuid4(),
        name="Acme",
        description=None,
        is_active=True,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _api_key(*, org_id: uuid.UUID, name: str = "Production") -> OrganizationApiKey:
    raw = "ape_live_testsecretvalue1234567890"
    return OrganizationApiKey(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        key_prefix=raw[:16],
        key_hash=hash_key(raw, PEPPER),
        revoked_at=None,
        last_used_at=None,
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
def api_key_repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    return mock


@pytest.fixture
def organization_repository() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def auth_events() -> AsyncMock:
    mock = AsyncMock()
    mock.publish = AsyncMock()
    return mock


@pytest.fixture
def service(
    session: AsyncMock,
    api_key_repository: AsyncMock,
    organization_repository: AsyncMock,
    auth_events: AsyncMock,
) -> ApiKeyService:
    return ApiKeyService(
        session=session,
        api_key_repository=api_key_repository,
        organization_repository=organization_repository,
        key_pepper=PEPPER,
        auth_events=auth_events,
    )


async def test_create_returns_secret_once(
    service: ApiKeyService,
    api_key_repository: AsyncMock,
    organization_repository: AsyncMock,
) -> None:
    org = _organization()
    organization_repository.get_by_id.return_value = org
    api_key_repository.exists_active_name.return_value = False
    api_key_repository.flush = AsyncMock()

    api_key, secret = await service.create(org.id, ApiKeyCreate(name="Production"))

    assert api_key.name == "Production"
    assert secret.startswith("ape_live_")
    assert verify_key(secret, PEPPER, api_key.key_hash)


async def test_create_duplicate_name_raises_conflict(
    service: ApiKeyService,
    organization_repository: AsyncMock,
    api_key_repository: AsyncMock,
) -> None:
    org = _organization()
    organization_repository.get_by_id.return_value = org
    api_key_repository.exists_active_name.return_value = True

    with pytest.raises(ConflictError) as exc_info:
        await service.create(org.id, ApiKeyCreate(name="Production"))
    assert exc_info.value.code == "api_key_name_conflict"


async def test_rotate_creates_new_key_without_revoking_old(
    service: ApiKeyService,
    api_key_repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org_id = uuid.uuid4()
    old_key = _api_key(org_id=org_id)
    api_key_repository.get_by_id_for_organization.return_value = old_key
    api_key_repository.exists_active_name.return_value = False
    api_key_repository.flush = AsyncMock()

    new_key, secret = await service.rotate(org_id, old_key.id, revoke_old=False)

    assert new_key.id != old_key.id
    assert old_key.revoked_at is None
    assert secret.startswith("ape_live_")
    auth_events.publish.assert_not_awaited()


async def test_revoke_sets_revoked_at(
    service: ApiKeyService,
    api_key_repository: AsyncMock,
    session: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org_id = uuid.uuid4()
    api_key = _api_key(org_id=org_id)
    api_key_repository.get_by_id_for_organization.return_value = api_key
    api_key_repository.flush = AsyncMock()

    result = await service.revoke(org_id, api_key.id)

    assert result.revoked_at is not None
    session.commit.assert_awaited_once()
    auth_events.publish.assert_awaited_once_with(ApiKeyAuthInvalidated(api_key.key_hash))


async def test_revoke_idempotent_does_not_publish_event(
    service: ApiKeyService,
    api_key_repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org_id = uuid.uuid4()
    api_key = _api_key(org_id=org_id)
    api_key.revoked_at = datetime.now(UTC)
    api_key_repository.get_by_id_for_organization.return_value = api_key
    api_key_repository.flush = AsyncMock()

    await service.revoke(org_id, api_key.id)

    auth_events.publish.assert_not_awaited()


async def test_rotate_with_revoke_old_publishes_invalidation(
    service: ApiKeyService,
    api_key_repository: AsyncMock,
    auth_events: AsyncMock,
) -> None:
    org_id = uuid.uuid4()
    old_key = _api_key(org_id=org_id)
    api_key_repository.get_by_id_for_organization.return_value = old_key
    api_key_repository.exists_active_name.return_value = False
    api_key_repository.flush = AsyncMock()

    await service.rotate(org_id, old_key.id, revoke_old=True)

    auth_events.publish.assert_awaited_once_with(ApiKeyAuthInvalidated(old_key.key_hash))


async def test_revoke_missing_key_raises_not_found(
    service: ApiKeyService, api_key_repository: AsyncMock
) -> None:
    api_key_repository.get_by_id_for_organization.return_value = None
    with pytest.raises(NotFoundError) as exc_info:
        await service.revoke(uuid.uuid4(), uuid.uuid4())
    assert exc_info.value.code == "api_key_not_found"


def test_rotation_name_truncates_long_base_names() -> None:
    long_name = "x" * 64
    rotated = _rotation_name(long_name)
    assert len(rotated) <= 64
    assert rotated.endswith("-rotated")


def test_rotation_name_adds_numeric_suffix_for_conflicts() -> None:
    base = "Production"
    assert _rotation_name(base) == "Production-rotated"
    assert _rotation_name(base, 2) == "Production-rotated-2"
