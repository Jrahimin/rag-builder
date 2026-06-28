"""Alembic migration environment (async).

The connection URL and target metadata are sourced from the application's own
configuration and declarative base, so migrations always agree with the
running application. Models must be imported (via ``app.models``) so that
autogenerate can see every table on ``Base.metadata``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (import side effect: register models on metadata)
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
target_metadata = Base.metadata

# Inject the async DSN from application settings.
config.set_main_option("sqlalchemy.url", settings.database.async_dsn)


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection ('offline' mode)."""
    context.configure(
        url=settings.database.async_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply migrations against a live DB using an async engine."""
    connectable = create_async_engine(
        settings.database.async_dsn,
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
