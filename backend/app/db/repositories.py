# app/db/repositories.py
#
# Repository classes for AnyMall-chan PostgreSQL layer.
#
# Why repositories?
#   Repositories are the ONLY code that knows about SQLAlchemy models and
#   sessions.  Everything above (agents, services, routes) receives plain
#   dicts — exactly the same shape file_store.py returned.
#   To swap storage again (e.g., to a different DB), only this file changes.
#
# Each repository receives an AsyncSession via constructor injection.
# The lifespan or get_session() creates the session, passes it down.
# Repositories never create their own sessions.
#
# Phase 1C repos: PetRepo, UserRepo, ActiveProfileRepo, FactLogRepo
# Phase 2 repos:  ThreadRepo, ThreadMessageRepo

import logging

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import (
    Pet, User, ActiveProfile, FactLog, Thread, ThreadMessage,
)
from app.types import ActiveProfileEntry

logger = logging.getLogger(__name__)


# ── PetRepo ──────────────────────────────────────────────────────────────────

class PetRepo:
    """Read/write pet_profile data (the `pets` table)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, pet_id: int) -> dict | None:
        """Read a pet profile by pet_id.  Returns dict or None if not found."""
        stmt = select(Pet).where(Pet.pet_id == pet_id)
        result = await self._session.execute(stmt)
        pet = result.scalar_one_or_none()
        return pet.to_dict() if pet else None

    async def upsert(self, data: dict) -> None:
        """
        Insert or update a pet profile.

        Uses PostgreSQL's ON CONFLICT DO UPDATE so this works whether
        the pet exists or not — no need to check first.
        """
        stmt = pg_insert(Pet).values(
            pet_id=data["pet_id"],
            name=data["name"],
            species=data.get("species", "dog"),
            breed=data.get("breed", "unknown"),
            date_of_birth=data.get("date_of_birth", "unknown"),
            sex=data.get("sex", "unknown"),
            life_stage=data.get("life_stage", "adult"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["pet_id"],
            set_={
                "name": stmt.excluded.name,
                "species": stmt.excluded.species,
                "breed": stmt.excluded.breed,
                "date_of_birth": stmt.excluded.date_of_birth,
                "sex": stmt.excluded.sex,
                "life_stage": stmt.excluded.life_stage,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()


# ── UserRepo ─────────────────────────────────────────────────────────────────

class UserRepo:
    """Read/write user_profile data (the `users` table)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, user_id: str) -> dict | None:
        """Read a user profile by user_id.  Returns dict or None if not found."""
        stmt = select(User).where(User.user_id == user_id)
        result = await self._session.execute(stmt)
        user = result.scalar_one_or_none()
        return user.to_dict() if user else None

    async def upsert(self, data: dict) -> None:
        """Insert or update a user profile."""
        stmt = pg_insert(User).values(
            user_id=data["user_id"],
            pet_id=data["pet_id"],
            session_count=data.get("session_count", 0),
            relationship_summary=data.get("relationship_summary", ""),
            updated_at=data.get("updated_at", ""),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "pet_id": stmt.excluded.pet_id,
                "session_count": stmt.excluded.session_count,
                "relationship_summary": stmt.excluded.relationship_summary,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()


# ── ActiveProfileRepo ────────────────────────────────────────────────────────

class ActiveProfileRepo:
    """
    Read/write active_profile data (the `active_profile` table).

    The critical method is read_all() which reconstructs a dict in the
    exact same shape as active_profile.json — a dict keyed by field_key.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_all(self, pet_id: int) -> dict[str, ActiveProfileEntry | str] | None:
        """
        Read all active_profile entries for a pet.

        Returns a dict matching active_profile.json shape:
          {
            "_pet_history": "narrative string...",
            "diet_type": {"value": "raw food", "confidence": 0.80, ...},
            ...
          }

        Returns None if no entries exist (triggers seeding on first startup).
        """
        stmt = select(ActiveProfile).where(ActiveProfile.pet_id == pet_id)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            return None

        profile: dict = {}
        for row in rows:
            profile[row.field_key] = row.to_dict_entry()

        return profile

    async def write_all(self, pet_id: int, profile_dict: dict[str, ActiveProfileEntry | str]) -> None:
        """
        Write the entire active_profile dict to the database.

        Strategy: DELETE all existing rows then INSERT fresh, within a single
        transaction (the session auto-begins one).  This means a concurrent
        reader between DELETE and INSERT would see an empty profile.  This is
        acceptable in Phase 1C (single pet, single user, asyncio Lock in
        Aggregator prevents concurrent writes).  For multi-user production,
        consider upsert-per-row or a serializable isolation level.

        Used for:
          - Seeding defaults on first startup
          - Aggregator write-through after merging facts

        Args:
            pet_id: The pet this profile belongs to.
            profile_dict: Full profile dict (same shape as active_profile.json).
        """
        # Delete existing rows for this pet.
        await self._session.execute(
            delete(ActiveProfile).where(ActiveProfile.pet_id == pet_id)
        )

        # Build all rows first, then add in bulk.
        rows: list[ActiveProfile] = []
        skipped = 0
        for field_key, entry in profile_dict.items():
            if field_key == "_pet_history":
                # _pet_history is a raw string, not a dict.
                rows.append(ActiveProfile(
                    pet_id=pet_id,
                    field_key=field_key,
                    value=entry if isinstance(entry, str) else str(entry),
                ))
            elif isinstance(entry, dict) and "value" in entry:
                # Regular fact entry with metadata.
                rows.append(ActiveProfile(
                    pet_id=pet_id,
                    field_key=field_key,
                    value=str(entry.get("value", "")),
                    confidence=entry.get("confidence"),
                    source_rank=entry.get("source_rank"),
                    time_scope=entry.get("time_scope"),
                    source_quote=entry.get("source_quote"),
                    updated_at=entry.get("updated_at"),
                    session_id=entry.get("session_id"),
                    status=entry.get("status"),
                    change_detected=entry.get("change_detected"),
                    trend_flag=entry.get("trend_flag"),
                ))
            else:
                # Skip unrecognized entries (defensive).
                logger.warning("Skipping unrecognized active_profile key: %s", field_key)
                skipped += 1
                continue

        self._session.add_all(rows)
        await self._session.commit()

        fact_count = sum(1 for r in rows if r.field_key != "_pet_history")
        has_history = any(r.field_key == "_pet_history" for r in rows)
        logger.debug(
            "active_profile: wrote %d fact entries%s for pet_id=%s (skipped %d)",
            fact_count,
            " + _pet_history" if has_history else "",
            pet_id,
            skipped,
        )


# ── FactLogRepo ──────────────────────────────────────────────────────────────

class FactLogRepo:
    """
    Append-only fact log (the `fact_log` table).

    Replaces file_store.append_fact_log() and read_fact_log().
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, facts: list[dict], pet_id: int) -> None:
        """
        Append a list of fact dicts to the fact_log table.

        Args:
            facts: List of fact dicts (same shape as what was appended
                   to fact_log.json by _run_background in chat.py).
            pet_id: The pet these facts belong to.
        """
        if not facts:
            return

        rows = [
            FactLog(
                pet_id=pet_id,
                session_id=fact.get("session_id", ""),
                field_key=fact.get("key", ""),
                value=str(fact.get("value", "")),
                confidence=float(fact.get("confidence", 0.0)),
                source_rank=fact.get("source_rank", "explicit_owner"),
                time_scope=fact.get("time_scope", "current"),
                uncertainty=fact.get("uncertainty", ""),
                source_quote=fact.get("source_quote", ""),
                timestamp=fact.get("timestamp"),
                needs_clarification=bool(fact.get("needs_clarification", False)),
                extracted_at=fact.get("extracted_at", ""),
            )
            for fact in facts
        ]
        self._session.add_all(rows)
        await self._session.commit()
        logger.debug("fact_log: appended %d facts for pet_id=%s", len(rows), pet_id)

    async def read_recent(
        self,
        pet_id: int,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Read recent fact_log entries, newest first.

        Args:
            pet_id: Filter by pet.
            session_id: Optional filter by session.
            limit: Max entries to return (capped by caller).

        Returns:
            List of fact dicts (same shape as fact_log.json entries).
        """
        stmt = select(FactLog).where(FactLog.pet_id == pet_id)

        if session_id:
            stmt = stmt.where(FactLog.session_id == session_id)

        stmt = stmt.order_by(FactLog.id.desc()).limit(limit)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [row.to_dict() for row in rows]


# ── ThreadRepo ────────────────────────────────────────────────────────────────

class ThreadRepo:
    """
    Thread lifecycle management (the `threads` table).

    Threads are 24-hour conversation windows. Each pet has at most one
    active thread at a time. Expired threads are kept for cross-thread
    context (compaction_summary from the previous thread).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        thread_id: str,
        pet_id: int,
        user_id: str,
        started_at: str,
        expires_at: str,
    ) -> dict:
        """Insert a new thread row.  Returns to_dict()."""
        thread = Thread(
            thread_id=thread_id,
            pet_id=pet_id,
            user_id=user_id,
            started_at=started_at,
            expires_at=expires_at,
            status="active",
        )
        self._session.add(thread)
        await self._session.commit()
        logger.info("Thread created: thread_id=%s pet_id=%s", thread_id, pet_id)
        return thread.to_dict()

    async def get_active(self, pet_id: int) -> dict | None:
        """
        Get the active thread for a pet.

        Returns to_dict() or None if no active thread exists.
        Does NOT check expires_at — caller is responsible for expiry logic
        so it can expire the thread explicitly before creating a new one.
        """
        stmt = (
            select(Thread)
            .where(Thread.pet_id == pet_id, Thread.status == "active")
            .limit(1)
        )
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        return thread.to_dict() if thread else None

    async def get_by_thread_id(self, thread_id: str) -> dict | None:
        """Get a thread by its ID (any status). Used by compaction."""
        stmt = select(Thread).where(Thread.thread_id == thread_id)
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        return thread.to_dict() if thread else None

    async def get_all_active(self) -> list[dict]:
        """Get all active threads.  Used at startup to reload into memory."""
        stmt = select(Thread).where(Thread.status == "active")
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def expire(self, thread_id: str) -> None:
        """Mark a thread as expired."""
        stmt = (
            select(Thread)
            .where(Thread.thread_id == thread_id)
        )
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        if thread:
            thread.status = "expired"
            await self._session.commit()
            logger.info("Thread expired: thread_id=%s", thread_id)

    async def update_compaction_summary(
        self, thread_id: str, summary: str,
        compacted_before_id: int | None = None,
    ) -> None:
        """Store the LLM-generated compaction summary and trim marker for a thread."""
        stmt = select(Thread).where(Thread.thread_id == thread_id)
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        if thread:
            thread.compaction_summary = summary
            if compacted_before_id is not None:
                thread.compacted_before_id = compacted_before_id
            await self._session.commit()
            logger.debug(
                "Compaction summary updated: thread_id=%s compacted_before_id=%s",
                thread_id, compacted_before_id,
            )

    async def get_latest_expired(self, pet_id: int) -> dict | None:
        """
        Get the most recently started expired thread for a pet.

        Used for cross-thread context — when a new thread starts, we load
        the previous thread's compaction_summary so Agent 1 has continuity.
        """
        stmt = (
            select(Thread)
            .where(Thread.pet_id == pet_id, Thread.status == "expired")
            .order_by(Thread.started_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        return thread.to_dict() if thread else None


# ── ThreadMessageRepo ─────────────────────────────────────────────────────────

class ThreadMessageRepo:
    """
    Read/write individual messages within a thread (the `thread_messages` table).

    Write-through pattern: messages are appended to app.state.sessions
    in-memory first (synchronous), then persisted here in _run_background()
    (fire-and-forget).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        thread_id: str,
        role: str,
        content: str,
        timestamp: str,
    ) -> None:
        """Insert a single message row."""
        msg = ThreadMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            timestamp=timestamp,
        )
        self._session.add(msg)
        await self._session.commit()

    async def append_batch(self, messages: list[dict]) -> None:
        """
        Insert multiple message rows in one transaction.

        Each dict must have: thread_id, role, content, timestamp.
        Used to write the user+assistant pair together in _run_background().
        """
        if not messages:
            return

        rows = [
            ThreadMessage(
                thread_id=msg["thread_id"],
                role=msg["role"],
                content=msg["content"],
                timestamp=msg["timestamp"],
            )
            for msg in messages
        ]
        self._session.add_all(rows)
        await self._session.commit()
        logger.debug(
            "thread_messages: appended %d messages to thread_id=%s",
            len(rows), messages[0]["thread_id"],
        )

    async def read_thread(
        self, thread_id: str, after_id: int | None = None,
    ) -> list[dict]:
        """
        Read messages for a thread, ordered by insertion order.

        Args:
            thread_id: The thread to read.
            after_id: If set, only return messages with id > after_id.
                      Used after compaction to skip summarized messages (W12).

        Returns list of {"role": ..., "content": ..., "timestamp": ...} dicts.
        """
        stmt = (
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
        )
        if after_id is not None:
            stmt = stmt.where(ThreadMessage.id > after_id)
        stmt = stmt.order_by(ThreadMessage.id.asc())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def get_compaction_cutoff_id(
        self, thread_id: str, keep_count: int,
    ) -> int | None:
        """
        Find the DB id of the last message to be compacted (W12).

        Returns the id of the message just before the 'keep_count' most recent
        messages. Returns None if not enough messages to compact.
        """
        from sqlalchemy import func
        count_stmt = (
            select(func.count())
            .select_from(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
        )
        total = (await self._session.execute(count_stmt)).scalar() or 0

        if total <= keep_count:
            return None

        # The last message that belongs in the "old" group
        offset = total - keep_count - 1
        stmt = (
            select(ThreadMessage.id)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.id.asc())
            .offset(offset)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
