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
# Phase 1C repos: UserRepo, ActiveProfileRepo, FactLogRepo
# Phase 2 repos:  ThreadRepo, ThreadMessageRepo

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, or_, select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import (
    User, ActiveProfile, FactLog, Thread, ThreadMessage, SuggestedQuestion,
)
from app.types import ActiveProfileEntry

logger = logging.getLogger(__name__)


# ── UserRepo ─────────────────────────────────────────────────────────────────

class UserRepo:
    """Read/write user data (the `users` table)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, user_code: str) -> dict | None:
        """Read a user profile by user_code.  Returns dict or None if not found."""
        stmt = select(User).where(User.user_code == user_code)
        result = await self._session.execute(stmt)
        user = result.scalar_one_or_none()
        return user.to_dict() if user else None

    async def update_relationship_summary(self, user_code: str, summary: str) -> None:
        """
        Targeted write of relationship_summary for a user.

        Called by RelationshipBuilder (via UserProfileWriter Protocol) after
        the nightly job builds a new summary from USER STYLE sections.

        Uses a targeted UPDATE rather than full upsert to avoid touching other
        user fields (session_count, preferred_language, etc.).
        """
        from sqlalchemy import update as sql_update
        now = datetime.now(timezone.utc).isoformat()
        stmt = (
            sql_update(User)
            .where(User.user_code == user_code)
            .values(relationship_summary=summary, updated_at=now)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        if result.rowcount == 0:
            logger.warning(
                "users: update_relationship_summary matched 0 rows — user_code=%s not found",
                user_code,
            )
        else:
            logger.debug(
                "users: updated relationship_summary for user_code=%s", user_code)

    async def upsert(self, data: dict) -> None:
        """Insert or update a user record.  Auto-called on every chat request."""
        insert_values: dict = dict(
            user_code=data["user_code"],
            display_name=data.get("display_name", ""),
            session_count=data.get("session_count", 0),
            relationship_summary=data.get("relationship_summary", ""),
            preferred_language=data.get("preferred_language", "auto"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        update_set: dict = {
            "display_name": "excluded.display_name",
            "session_count": "excluded.session_count",
            "relationship_summary": "excluded.relationship_summary",
            "preferred_language": "excluded.preferred_language",
            "updated_at": "excluded.updated_at",
        }

        # Write last_known_pet_ids when caller provides all_pet_ids
        if "all_pet_ids" in data and data["all_pet_ids"] is not None:
            insert_values["last_known_pet_ids"] = data["all_pet_ids"]

        stmt = pg_insert(User).values(**insert_values)
        on_conflict_set = {
            "display_name": stmt.excluded.display_name,
            "session_count": stmt.excluded.session_count,
            "relationship_summary": stmt.excluded.relationship_summary,
            "preferred_language": stmt.excluded.preferred_language,
            "updated_at": stmt.excluded.updated_at,
        }
        if "all_pet_ids" in data and data["all_pet_ids"] is not None:
            on_conflict_set["last_known_pet_ids"] = stmt.excluded.last_known_pet_ids

        stmt = stmt.on_conflict_do_update(
            index_elements=["user_code"],
            set_=on_conflict_set,
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

    async def write_all(self, pet_id: int, profile_dict: dict[str, ActiveProfileEntry | str], user_code: str = "") -> None:
        """
        Write the entire active_profile dict to the database.

        Strategy: upsert each row (INSERT ON CONFLICT DO UPDATE) then delete
        stale keys no longer in the new profile — all within one transaction.

        Why not DELETE+INSERT?
          DELETE then INSERT creates an empty window between the two statements.
          Even though both are in the same transaction, a concurrent writer
          (second background pipeline for the same pet) can issue its own DELETE
          after ours, then INSERT only its own facts — silently discarding ours
          when it commits.  Upsert eliminates this: each field_key is atomically
          replaced and the profile is never empty.

        Used for:
          - Seeding defaults on first startup
          - Aggregator write-through after merging facts

        Args:
            pet_id: The pet this profile belongs to.
            profile_dict: Full profile dict (same shape as active_profile.json).
        """
        new_keys: set[str] = set()
        skipped = 0

        for field_key, entry in profile_dict.items():
            if field_key in ("_pet_history", "_history_last_updated"):
                # Plain string rows — no metadata columns.
                values = dict(
                    pet_id=pet_id,
                    user_code=user_code,
                    field_key=field_key,
                    value=entry if isinstance(entry, str) else str(entry),
                )
                update_set = {"value": values["value"], "user_code": user_code}
            elif isinstance(entry, dict) and "value" in entry:
                values = dict(
                    pet_id=pet_id,
                    user_code=user_code,
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
                )
                update_set = {k: values[k] for k in values if k not in ("pet_id", "field_key")}
            else:
                logger.warning("Skipping unrecognized active_profile key: %s", field_key)
                skipped += 1
                continue

            stmt = pg_insert(ActiveProfile).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["pet_id", "field_key"],
                set_=update_set,
            )
            await self._session.execute(stmt)
            new_keys.add(field_key)

        # Delete rows for keys that no longer exist in the new profile.
        # This is the only DELETE — it removes stale fields, not all fields.
        if new_keys:
            await self._session.execute(
                delete(ActiveProfile).where(
                    ActiveProfile.pet_id == pet_id,
                    ActiveProfile.field_key.not_in(new_keys),
                )
            )

        await self._session.commit()

        _internal_keys = {"_pet_history", "_history_last_updated"}
        fact_count = sum(1 for k in new_keys if k not in _internal_keys)
        has_history = "_pet_history" in new_keys
        logger.debug(
            "active_profile: wrote %d fact entries%s for pet_id=%s (skipped %d)",
            fact_count,
            " + _pet_history" if has_history else "",
            pet_id,
            skipped,
        )

    async def write_history(self, pet_id: int, history: str, last_updated: str, user_code: str = "") -> None:
        """
        Upsert the _pet_history and _history_last_updated rows for a pet.

        Called by HistoryBuilder after generating a new narrative. Uses
        ON CONFLICT DO UPDATE so the rows are created on first call and
        overwritten on every subsequent call.
        """
        for field_key, value in (
            ("_pet_history", history),
            ("_history_last_updated", last_updated),
        ):
            stmt = pg_insert(ActiveProfile).values(
                pet_id=pet_id,
                user_code=user_code,
                field_key=field_key,
                value=value,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["pet_id", "field_key"],
                set_={"value": stmt.excluded.value,
                      "user_code": stmt.excluded.user_code},
            )
            await self._session.execute(stmt)

        await self._session.commit()
        logger.debug(
            "active_profile: wrote _pet_history + _history_last_updated for pet_id=%s",
            pet_id,
        )


# ── FactLogRepo ──────────────────────────────────────────────────────────────

class FactLogRepo:
    """
    Append-only fact log (the `fact_log` table).

    Replaces file_store.append_fact_log() and read_fact_log().
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, facts: list[dict], pet_id: int, user_code: str = "") -> None:
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
                user_code=user_code,
                session_id=fact.get("session_id", ""),
                field_key=fact.get("key", ""),
                value=str(fact.get("value", "")),
                confidence=float(fact.get("confidence", 0.0)),
                source_rank=fact.get("source_rank", "explicit_owner"),
                time_scope=fact.get("time_scope", "current"),
                uncertainty=fact.get("uncertainty", ""),
                source_quote=fact.get("source_quote", ""),
                timestamp=fact.get("timestamp"),
                needs_clarification=bool(
                    fact.get("needs_clarification", False)),
                pet_label=fact.get("pet_label", "pet_a"),
                extracted_at=fact.get("extracted_at", ""),
            )
            for fact in facts
        ]
        self._session.add_all(rows)
        await self._session.commit()
        logger.debug("fact_log: appended %d facts for pet_id=%s",
                     len(rows), pet_id)

    async def append_bulk(self, facts_with_pets: list[tuple[list[dict], int]], user_code: str = "") -> None:
        """
        Append facts for multiple pets in a single atomic commit.

        Used by Task 6 batching in background.py to ensure dual-pet fact writes
        are all-or-nothing. Unlike append(), this method does NOT commit after each
        pet — one commit covers all pets, so a failure on Pet B cannot leave Pet A's
        facts permanently written while Pet B's are silently lost.

        Args:
            facts_with_pets: List of (facts_list, pet_id) tuples, one per pet.
        """
        all_rows: list[FactLog] = []
        for facts, pet_id in facts_with_pets:
            all_rows.extend([
                FactLog(
                    pet_id=pet_id,
                    user_code=user_code,
                    session_id=fact.get("session_id", ""),
                    field_key=fact.get("key", ""),
                    value=str(fact.get("value", "")),
                    confidence=float(fact.get("confidence", 0.0)),
                    source_rank=fact.get("source_rank", "explicit_owner"),
                    time_scope=fact.get("time_scope", "current"),
                    uncertainty=fact.get("uncertainty", ""),
                    source_quote=fact.get("source_quote", ""),
                    timestamp=fact.get("timestamp"),
                    needs_clarification=bool(
                        fact.get("needs_clarification", False)),
                    pet_label=fact.get("pet_label", "pet_a"),
                    extracted_at=fact.get("extracted_at", ""),
                )
                for fact in facts
            ])
        if all_rows:
            self._session.add_all(all_rows)
            await self._session.commit()
            logger.debug(
                "fact_log: bulk appended %d facts across %d pet(s)",
                len(all_rows), len(facts_with_pets),
            )

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

    async def read_since(
        self,
        pet_id: int,
        since: str,
        confidence_floor: float = 0.70,
    ) -> list[dict]:
        """
        Read high-confidence facts extracted after a given timestamp.

        Used by HistoryBuilder to fetch only the facts that are new since
        the last history build (pointed to by _history_last_updated).

        Args:
            pet_id: Filter by pet.
            since: ISO timestamp string — return facts extracted after this time.
            confidence_floor: Exclusive lower bound (default 0.70 — matches pipeline
                              definition of "high": confidence > 0.70).

        Returns list of fact dicts ordered oldest-first (for chronological narrative).
        """
        stmt = (
            select(FactLog)
            .where(
                FactLog.pet_id == pet_id,
                FactLog.extracted_at > since,
                # Strictly > to match the pipeline's "high" definition (background.py: > 0.70).
                # Using >= would include exactly-0.70 facts which are classified as "low"
                # (pending clarification) — wrong to bake unconfirmed facts into history.
                FactLog.confidence > confidence_floor,
            )
            .order_by(FactLog.extracted_at.asc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def count_distinct_sessions_since(self, pet_id: int, since: str) -> int:
        """
        Count distinct session_ids in fact_log after a given timestamp.

        Used by HistoryBuilder hybrid trigger — Condition B fires when
        >= 2 sessions have passed since the last history build.
        fact_log already has session_id on every row, so no separate counter needed.

        Args:
            pet_id: Filter by pet.
            since: ISO timestamp string — count sessions with activity after this time.

        Returns count of distinct session_id values.
        """
        stmt = (
            select(func.count(func.distinct(FactLog.session_id)))
            .where(
                FactLog.pet_id == pet_id,
                FactLog.extracted_at > since,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0


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
        secondary_pet_id: int | None = None,
    ) -> dict:
        """Insert a new thread row.  Returns to_dict()."""
        thread = Thread(
            thread_id=thread_id,
            pet_id=pet_id,
            secondary_pet_id=secondary_pet_id,
            user_id=user_id,
            started_at=started_at,
            expires_at=expires_at,
            status="active",
        )
        self._session.add(thread)
        await self._session.commit()
        logger.info("Thread created: thread_id=%s pet_id=%s secondary=%s",
                    thread_id, pet_id, secondary_pet_id)
        return thread.to_dict()

    async def get_active(self, pet_id: int) -> dict | None:
        """
        Get the active thread for a pet.

        Searches both pet_id and secondary_pet_id (W11 — dual-pet threads).
        This means a pet that is the secondary on a thread will still find that
        thread. The caller (chat.py) uses thread_id for all subsequent operations,
        so the primary/secondary distinction doesn't matter after lookup.
        Returns to_dict() or None if no active thread exists.
        Does NOT check expires_at — caller is responsible for expiry logic
        so it can expire the thread explicitly before creating a new one.
        """
        stmt = (
            select(Thread)
            .where(
                or_(Thread.pet_id == pet_id, Thread.secondary_pet_id == pet_id),
                Thread.status == "active",
            )
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

    async def update_secondary_pet_id(self, thread_id: str, secondary_pet_id: int) -> None:
        """Set secondary_pet_id on an existing thread (W11 — dual-pet upgrade)."""
        stmt = select(Thread).where(Thread.thread_id == thread_id)
        result = await self._session.execute(stmt)
        thread = result.scalar_one_or_none()
        if thread:
            thread.secondary_pet_id = secondary_pet_id
            await self._session.commit()
            logger.debug("Thread %s: set secondary_pet_id=%s",
                         thread_id, secondary_pet_id)

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

    async def get_expired_unsummarized(self, hours_back: int = 48) -> list[dict]:
        """
        Find threads that have passed their expiry time and have no compaction_summary.

        These are threads that ended before hitting THREAD_COMPACTION_THRESHOLD
        (50 messages), so _run_compaction() was never triggered. The nightly
        closing-summary job uses this to backfill them.

        Does NOT filter on status == 'expired' because threads are only marked
        expired lazily on the next chat request. A thread whose expires_at has
        passed but whose user never returned stays status='active' indefinitely.

        Args:
            hours_back: Look back this many hours (default 48 — covers last two nights).

        Returns list of thread dicts.
        """
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) -
                  timedelta(hours=hours_back)).isoformat()
        stmt = (
            select(Thread)
            .where(
                Thread.compaction_summary.is_(None),
                # Thread has actually passed its expiry time (not necessarily status='expired')
                Thread.expires_at <= now,
                # But only look back hours_back hours — ignore ancient threads
                Thread.expires_at > cutoff,
            )
            .order_by(Thread.expires_at.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def get_recent_for_user(self, user_code: str, limit: int = 5) -> list[dict]:
        """
        Get the most recent threads with a compaction_summary for a user.

        Used by RelationshipBuilder to read USER STYLE sections from
        recent conversations and build users.relationship_summary.

        Args:
            user_code: The user to query (matches threads.user_id).
            limit: Max threads to return (default 5 — last 5 conversations).

        Returns list of thread dicts, newest first.
        """
        stmt = (
            select(Thread)
            .where(
                Thread.user_id == user_code,
                Thread.compaction_summary.isnot(None),
            )
            .order_by(Thread.started_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def get_expired_with_summary_since(self, hours_back: int = 24) -> list[dict]:
        """
        Find threads that passed their expiry time in the last `hours_back` hours
        AND have a compaction_summary.

        Used by the nightly RelationshipBuilder job to find users who had
        activity since the last run — avoids scanning the entire threads table.

        Does NOT filter on status == 'expired' — see get_expired_unsummarized docstring
        for the lazy-expiry reasoning.

        Args:
            hours_back: Look back this many hours (default 24 — since last midnight).

        Returns list of thread dicts (deduplicate user_ids in the caller).
        """
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) -
                  timedelta(hours=hours_back)).isoformat()
        stmt = (
            select(Thread)
            .where(
                Thread.compaction_summary.isnot(None),
                Thread.expires_at <= now,
                Thread.expires_at > cutoff,
            )
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [row.to_dict() for row in rows]

    async def get_latest_expired(self, pet_id: int) -> dict | None:
        """
        Get the most recently started expired thread for a pet.

        Searches both pet_id and secondary_pet_id (W11 — dual-pet threads).
        Used for cross-thread context — when a new thread starts, we load
        the previous thread's compaction_summary so Agent 1 has continuity.
        """
        stmt = (
            select(Thread)
            .where(
                or_(Thread.pet_id == pet_id, Thread.secondary_pet_id == pet_id),
                Thread.status == "expired",
            )
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


# ── SuggestedQuestionsRepo ────────────────────────────────────────────────────

class SuggestedQuestionsRepo:
    """
    Read/write the anymall_chan_suggested_questions table.

    Per-pet cold storage: one row per (user_code, language, pet_id).
    Each pet has its own 10-question set (3 dedicated + 1 both per module).
    Valkey is the hot cache (10-day TTL, keyed by pet_id); this table is the
    fallback so questions survive Valkey TTL expiry without triggering an LLM call.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        user_code: str,
        language: str,
        pet_id: int,
        questions: list[dict],
        generated_at: datetime,
    ) -> None:
        """
        Insert or update the suggested questions for a user+language+pet.

        ON CONFLICT (user_code, language, pet_id) DO UPDATE — always overwrites
        with the freshest generation result.
        """
        stmt = pg_insert(SuggestedQuestion).values(
            user_code=user_code,
            language=language,
            pet_id=pet_id,
            questions=questions,
            generated_at=generated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_code", "language", "pet_id"],
            set_={
                "questions": stmt.excluded.questions,
                "generated_at": stmt.excluded.generated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()
        logger.debug(
            "suggested_questions: upserted %d questions for user=%s lang=%s pet_id=%s",
            len(questions), user_code, language, pet_id,
        )

    async def get(self, user_code: str, language: str, pet_id: int) -> dict | None:
        """
        Fetch the stored questions for a user+language+pet.

        Returns {"questions": [...], "generated_at": datetime} or None if not found.
        """
        stmt = (
            select(SuggestedQuestion)
            .where(
                SuggestedQuestion.user_code == user_code,
                SuggestedQuestion.language == language,
                SuggestedQuestion.pet_id == pet_id,
            )
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "questions": row.questions,
            "generated_at": row.generated_at,
        }

    async def get_all_stale(self, age_days: int = 7) -> list[dict]:
        """
        Find all per-pet rows that need regen.

        Two conditions (either triggers regen):
          1. generated_at < NOW() - age_days  (weekly refresh)
          2. A high-confidence fact was written AFTER the questions were generated
             (profile changed → questions are stale)

        Returns one dict per (user_code, language, pet_id) row — the nightly job
        calls regen_for_user once per row.

        CRITICAL: The IS NOT NULL guard on lf.last_fact_at is mandatory.
        Without it, every user with zero high-confidence facts would satisfy
        'NULL < generated_at' which is always false — but COALESCE(NULL, NOW())
        would make it always true. The IS NOT NULL guard ensures we only regen
        when a real fact timestamp exists.
        """
        sql = text(f"""
            WITH last_fact AS (
                SELECT user_code, MAX(created_at) AS last_fact_at
                FROM anymall_chan_fact_log
                WHERE confidence >= 0.8
                GROUP BY user_code
            )
            SELECT sq.user_code, sq.language, sq.pet_id, u.last_known_pet_ids
            FROM anymall_chan_suggested_questions sq
            JOIN anymall_chan_users u ON sq.user_code = u.user_code
            LEFT JOIN last_fact lf ON sq.user_code = lf.user_code
            WHERE sq.language = u.preferred_language
              AND (
                sq.generated_at < NOW() - INTERVAL '{age_days} days'
                OR (lf.last_fact_at IS NOT NULL AND sq.generated_at < lf.last_fact_at)
              )
        """)
        result = await self._session.execute(sql)
        rows = result.fetchall()
        return [
            {
                "user_code": row[0],
                "language": row[1],
                "pet_id": row[2],
                "last_known_pet_ids": row[3] or [],
            }
            for row in rows
        ]

    async def cleanup_stale_language_rows(self) -> int:
        """
        Delete suggested-question rows whose language no longer matches the user's
        preferred_language. Called by the nightly job after regen to keep storage clean.

        Returns the number of rows deleted.

        Why this is safe: the nightly job only regens rows that match preferred_language
        (via get_all_stale), so by the time this runs the current language already has
        fresh rows. Deleting the old-language rows will never leave the user with no
        questions — /setup will always find the current-language rows.
        """
        sql = text("""
            DELETE FROM anymall_chan_suggested_questions sq
            USING anymall_chan_users u
            WHERE sq.user_code = u.user_code
              AND sq.language != u.preferred_language
        """)
        result = await self._session.execute(sql)
        await self._session.commit()
        return result.rowcount
