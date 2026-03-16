# app/db/session.py
#
# Async SQLAlchemy engine + session factory for Phase 1C.
#
# Why async?
#   FastAPI is async. Using sync SQLAlchemy would block the event loop on
#   every DB call — defeating the purpose of async.  asyncpg is the fastest
#   async PostgreSQL driver for Python.
#
# Three public functions:
#   init_db(url)       — called once at startup (creates engine + session factory)
#   dispose_engine()   — called once at shutdown (closes connection pool)
#   get_session()      — async context manager yielding a session (used by repos)
#
# Usage:
#   from app.db.session import get_session
#   async with get_session() as session:
#       result = await session.execute(select(Pet))

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

# Module-level references — set by init_db(), used by get_session().
# These are NOT globals you import directly.  Always go through get_session().
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(database_url: str) -> AsyncEngine:
    """
    Create the async engine and session factory.  Called once at startup.

    Verifies connectivity by executing SELECT 1.  If PostgreSQL is not
    running, this raises immediately — fail fast with a clear error instead
    of a mysterious "connection refused" 20 minutes later.

    Args:
        database_url: PostgreSQL connection string.
            Example: postgresql+asyncpg://anymall:anymall_dev@localhost:5432/anymallchan

    Returns:
        The engine so the caller (lifespan) can store it for shutdown.
    """
    global _engine, _session_factory

    # Railway/Render provide postgresql:// but asyncpg needs postgresql+asyncpg://
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    _engine = create_async_engine(
        database_url,
        echo=False,       # True = log every SQL statement (noisy, useful for debugging)
        pool_size=5,      # Max persistent connections (reasonable for single-server dev)
        max_overflow=10,  # Extra connections allowed under burst load
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,  # Prevent lazy-load errors after commit
    )

    # Fail fast: verify the database is reachable.
    async with _engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    # Log host only — never log the password.
    safe_url = database_url.split("@")[-1] if "@" in database_url else "???"
    logger.info("Database connected: %s", safe_url)

    return _engine


async def dispose_engine() -> None:
    """Close the connection pool.  Called once at shutdown in lifespan."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed.")
    _engine = None
    _session_factory = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async session.  Used by repositories and background tasks.

    Each session is a short-lived unit of work — open it, do some queries,
    commit (or rollback), close.  The async with block handles cleanup.

    Important: sessions do NOT auto-commit.  Callers (or repo methods) must
    call ``await session.commit()`` explicitly after write operations.  If
    the context manager exits without a commit, changes are rolled back.

    Raises RuntimeError if init_db() has not been called yet.

    Usage:
        async with get_session() as session:
            repo = PetRepo(session)
            pet = await repo.read("luna-001")
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database not initialised — call init_db() first. "
            "Is PostgreSQL running? (docker compose up -d)"
        )

    async with _session_factory() as session:
        yield session
