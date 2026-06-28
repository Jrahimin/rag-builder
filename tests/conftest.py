"""Shared pytest fixtures.

Forces the ``testing`` environment *before* importing the application so a
developer's local ``.env`` never bleeds into the test run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

os.environ.setdefault("APE_APP__ENV", "testing")
os.environ.setdefault("APE_LOGGING__RENDER_JSON", "false")

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the ASGI app with lifespan executed.

    The lifespan is resilient to unavailable dependencies, so this works even
    when no external services (Postgres/Redis/Qdrant/MinIO) are running.
    """
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
