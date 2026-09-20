"""Real-pool checks for request-local database acquisition instrumentation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, text

from app.core.config import Settings
from app.platform.db.session import Database
from app.platform.providers.request_work import RequestWork

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_observed_session_counts_real_checkouts_without_forcing_empty_flush(
    require_postgres: None,
    apply_migrations: None,
    settings: Settings,
) -> None:
    database = Database(settings)
    checkout_count = 0

    def record_checkout(*_args: object) -> None:
        nonlocal checkout_count
        checkout_count += 1

    event.listen(database.engine.sync_engine, "checkout", record_checkout)
    work = RequestWork(uuid.uuid4())
    try:
        async with database.session_factory() as session:
            with work.attached():
                await session.flush()
                assert checkout_count == 0
                assert work.counts["database_connection_acquisition_waits"] == 0

                assert await session.scalar(text("SELECT 1")) == 1
                await session.execute(text("SELECT 2"))
                assert checkout_count == 1
                assert work.counts["database_connection_acquisition_waits"] == 1

                await session.commit()
                streamed = await session.stream(text("SELECT 3"))
                assert await streamed.scalar() == 3
                assert checkout_count == 2
                assert work.counts["database_connection_acquisition_waits"] == 2

                assert await session.scalar(text("SELECT 4")) == 4
                assert checkout_count == 2
                assert work.counts["database_connection_acquisition_waits"] == 2
    finally:
        event.remove(database.engine.sync_engine, "checkout", record_checkout)
        await database.dispose()
