# migrations/env.py
#
# Alembic environment configuration for Phase 1C.
#
# Key changes from the default async template:
#   1. Imports Base.metadata from app.db.models — so autogenerate works
#   2. Reads DATABASE_URL from .env via our Settings — single source of truth
#   3. Overrides sqlalchemy.url in alembic.ini with the Settings value

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Our imports ──────────────────────────────────────────────────────────────
# Import Base so Alembic can discover all ORM models for autogenerate.
from app.db.models import Base
from app.core.config import settings

# ── Alembic config ──────────────────────────────────────────────────────────
config = context.config

# Override the URL from alembic.ini with our Settings value (reads .env).
# This ensures alembic.ini doesn't need to be kept in sync manually.
if settings.database_url:
    config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up Python logging from alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tell Alembic about our table definitions — enables autogenerate.
target_metadata = Base.metadata


# ── Offline mode (generates SQL script without connecting) ──────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connects to the database and runs migrations) ──────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations through it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
