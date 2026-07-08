"""Unit tests for VerifiedKeyCacheEventHandler."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.platform.auth.events import ApiKeyAuthInvalidated, OrganizationAuthInvalidated
from app.platform.infra.auth.verified_key_cache_event_handler import VerifiedKeyCacheEventHandler

pytestmark = pytest.mark.unit


@pytest.fixture
def cache() -> AsyncMock:
    mock = AsyncMock()
    mock.invalidate = AsyncMock()
    mock.invalidate_organization = AsyncMock()
    return mock


async def test_publishes_organization_invalidation(cache: AsyncMock) -> None:
    org_id = uuid.uuid4()
    handler = VerifiedKeyCacheEventHandler(cache)

    await handler.publish(OrganizationAuthInvalidated(org_id))

    cache.invalidate_organization.assert_awaited_once_with(org_id)
    cache.invalidate.assert_not_awaited()


async def test_publishes_api_key_invalidation(cache: AsyncMock) -> None:
    handler = VerifiedKeyCacheEventHandler(cache)

    await handler.publish(ApiKeyAuthInvalidated("hash-abc"))

    cache.invalidate.assert_awaited_once_with("hash-abc")
    cache.invalidate_organization.assert_not_awaited()


async def test_noop_when_cache_unconfigured() -> None:
    handler = VerifiedKeyCacheEventHandler(None)
    await handler.publish(OrganizationAuthInvalidated(uuid.uuid4()))
