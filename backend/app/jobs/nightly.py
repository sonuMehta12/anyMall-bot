# app/jobs/nightly.py
#
# APScheduler entry point — called at midnight UTC via AsyncIOScheduler.
#
# What lives here:
#   - run_nightly_jobs()                    — top-level entry point (called by scheduler)
#   - _close_expired_thread_summaries()     — Task 3: backfill summaries for short threads
#   - _rebuild_relationship_summaries()     — Task 4: update users.relationship_summary
#   - _extract_user_style()                 — helper: parse USER STYLE: section from summary
#
# Each job function catches its own exceptions so one failure never aborts the others.
# All functions are async — they run inside the existing asyncio event loop via
# AsyncIOScheduler (no new threads needed).

# ── Standard library ────────────────────────────────────────────────────────
import logging
from typing import Any

# ── Our code ────────────────────────────────────────────────────────────────
from app.cache.keys import CacheKeys
from app.db.session import get_session
from app.db.repositories import ThreadRepo, ThreadMessageRepo, UserRepo

logger = logging.getLogger(__name__)


async def run_nightly_jobs(app_state: Any) -> None:
    """
    Entry point called by APScheduler at midnight UTC.

    Runs all nightly maintenance jobs in sequence. Each job catches its own
    exceptions — a failure in one job never prevents the others from running.
    """
    logger.info("Nightly jobs starting")
    # Each job is wrapped independently — a crash in one never prevents the other from running.
    try:
        await _close_expired_thread_summaries(app_state)
    except Exception as exc:
        logger.error("Nightly job _close_expired_thread_summaries failed: %s", exc)
    try:
        await _rebuild_relationship_summaries(app_state)
    except Exception as exc:
        logger.error("Nightly job _rebuild_relationship_summaries failed: %s", exc)
    logger.info("Nightly jobs complete")


async def _close_expired_thread_summaries(app_state: Any) -> None:
    """
    Find threads that expired without hitting 50 messages (never triggered compaction),
    run ThreadSummarizer on their messages, and write compaction_summary so
    RelationshipBuilder can read the USER STYLE section the next day.

    Why nightly only (not reactive):
        Running this on the next user message would add an LLM call before
        the user gets their first reply in the new session — wrong place.
        Nightly is the correct time: all yesterday's threads are closed,
        tomorrow's RelationshipBuilder job has fresh summaries to read.
    """
    thread_summarizer = getattr(app_state, "thread_summarizer", None)
    if thread_summarizer is None:
        logger.warning("_close_expired_thread_summaries: thread_summarizer not available")
        return

    async with get_session() as db_session:
        thread_repo = ThreadRepo(db_session)
        threads = await thread_repo.get_expired_unsummarized(hours_back=48)

    if not threads:
        logger.info("Closing summaries: no unsummarized expired threads found")
        return

    logger.info("Closing summaries: processing %d thread(s)", len(threads))
    written = 0
    for thread in threads:
        thread_id = thread["thread_id"]
        try:
            async with get_session() as db_session:
                msg_repo = ThreadMessageRepo(db_session)
                messages = await msg_repo.read_thread(thread_id)

            if not messages:
                logger.debug("Closing summary skipped — no messages in thread=%s", thread_id)
                continue

            summary = await thread_summarizer.summarize(messages, existing_summary=None)

            async with get_session() as db_session:
                thread_repo = ThreadRepo(db_session)
                await thread_repo.update_compaction_summary(thread_id, summary)

            written += 1
            logger.info(
                "Closing summary written — thread=%s messages=%d",
                thread_id, len(messages),
            )
        except Exception as exc:
            logger.error("Closing summary failed — thread=%s error=%s", thread_id, exc)
            # Continue to next thread — never abort the whole job

    logger.info("Closing summaries: wrote %d / %d summaries", written, len(threads))


def _extract_user_style(compaction_summary: str) -> str:
    """
    Extract the USER STYLE: section from an enhanced two-section summary.

    Returns "" for old-format summaries (before Task 1 prompt enhancement)
    so RelationshipBuilder skips them cleanly.
    """
    marker = "USER STYLE:"
    idx = compaction_summary.find(marker)
    if idx == -1:
        return ""
    return compaction_summary[idx + len(marker):].strip()


async def _rebuild_relationship_summaries(app_state: Any) -> None:
    """
    For users who had a thread close in the last 24h WITH a compaction_summary,
    read USER STYLE sections from their last 3-5 summaries and merge them into
    users.relationship_summary via RelationshipBuilder.

    Only processes users with at least one enhanced-format summary (one that
    contains a USER STYLE: section). Users with only old-format summaries
    are skipped until they accumulate new-format ones.
    """
    relationship_builder = getattr(app_state, "relationship_builder", None)
    if relationship_builder is None:
        logger.debug("_rebuild_relationship_summaries: relationship_builder not available")
        return

    # ── Find users with thread activity in the last 24h ─────────────────
    async with get_session() as db_session:
        thread_repo = ThreadRepo(db_session)
        recent_threads = await thread_repo.get_expired_with_summary_since(hours_back=24)

    if not recent_threads:
        logger.info("Relationship summaries: no users with recent thread activity")
        return

    # Deduplicate to unique user_codes
    user_codes = list({t["user_id"] for t in recent_threads if t.get("user_id")})
    logger.info("Relationship summaries: processing %d user(s)", len(user_codes))

    updated = 0
    for user_code in user_codes:
        try:
            # ── Load last 5 summaries for this user ────────────────────
            async with get_session() as db_session:
                thread_repo = ThreadRepo(db_session)
                summaries = await thread_repo.get_recent_for_user(user_code, limit=5)

            # Parse USER STYLE sections; skip old-format summaries (return "")
            style_obs = [
                _extract_user_style(t["compaction_summary"])
                for t in summaries
                if t.get("compaction_summary")
            ]
            style_obs = [s for s in style_obs if s]  # filter empty (old format)

            if not style_obs:
                logger.debug(
                    "Relationship summary skipped — no USER STYLE sections yet for user=%s",
                    user_code,
                )
                continue

            # ── Load current relationship_summary ──────────────────────
            async with get_session() as db_session:
                user_repo = UserRepo(db_session)
                user = await user_repo.read(user_code)

            existing = (user or {}).get("relationship_summary", "") or ""

            # ── Build updated summary ──────────────────────────────────
            new_summary = await relationship_builder.build(user_code, style_obs, existing)

            # ── Write via RelationshipBuilder public method ────────────
            # Calls _writer.update_relationship_summary internally — callers
            # never touch _writer directly so the Protocol abstraction holds.
            await relationship_builder.write_summary(user_code, new_summary)

            # ── Invalidate Valkey user cache ───────────────────────────
            vk = getattr(app_state, "valkey", None)
            if vk is not None:
                await vk.delete(CacheKeys.user(user_code))

            updated += 1
            logger.info("Relationship summary updated — user=%s", user_code)

        except Exception as exc:
            logger.error(
                "Relationship rebuild failed — user=%s error=%s", user_code, exc,
            )
            # Continue to next user — never abort the whole job

    logger.info(
        "Relationship summaries: updated %d / %d users", updated, len(user_codes),
    )
