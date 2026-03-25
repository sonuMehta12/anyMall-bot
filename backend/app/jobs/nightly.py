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
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Third-party ─────────────────────────────────────────────────────────────
from sqlalchemy import func, select

# ── Our code ────────────────────────────────────────────────────────────────
from app.cache.keys import CacheKeys, TTL_SUGGESTED, TTL_SUGGESTED_HISTORY, jittered_ttl
from app.core.config import settings
from app.db.models import ActiveProfile, Thread
from app.db.repositories import ActiveProfileRepo, ThreadMessageRepo, ThreadRepo, UserRepo
from app.db.session import get_session
from app.services.context_builder import build_pet_context
from app.services.pet_fetcher import PetFetchError
from app.services.question_templates import get_evergreen_questions
from app.services.question_validator import validate_questions

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
    try:
        await _pregenerate_suggested_questions(app_state)
    except Exception as exc:
        logger.error("Nightly job _pregenerate_suggested_questions failed: %s", exc)
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


# ── Task 5: Pre-generate suggested questions ───────────────────────────────

async def _pregenerate_suggested_questions(app_state: Any) -> None:
    """
    Pre-generate suggested home screen questions for all active users.

    For each user with threads in the last 30 days:
      1. Check if cached questions exist and are still fresh
         (generated_at > max(active_profile.updated_at))
      2. If stale or missing → generate via SuggestedQuestionsAgent → validate → cache

    Uses the cheap/fast LLM model configured in settings.openai_model_suggestions.
    Runs after relationship summaries so any profile updates from this job cycle
    are already committed.
    """
    sq_agent = getattr(app_state, "suggested_questions_agent", None)
    if sq_agent is None:
        logger.info("_pregenerate_suggested_questions: agent not available, skipping")
        return

    vk = getattr(app_state, "valkey", None)
    if vk is None:
        logger.info("_pregenerate_suggested_questions: valkey not available, skipping")
        return

    pet_fetcher = getattr(app_state, "pet_fetcher", None)
    if pet_fetcher is None:
        logger.info("_pregenerate_suggested_questions: pet_fetcher not available, skipping")
        return

    # ── 1. Find active users with their pet combinations ─────────────────
    active_combos: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        async with get_session() as db_session:
            stmt = (
                select(
                    Thread.user_id,
                    Thread.pet_id,
                    Thread.secondary_pet_id,
                )
                .where(Thread.started_at > cutoff.isoformat())
                .distinct()
            )
            result = await db_session.execute(stmt)
            for row in result.fetchall():
                pet_ids = [row[1]]  # primary pet_id
                if row[2]:          # secondary_pet_id
                    pet_ids.append(row[2])
                active_combos.append({
                    "user_code": row[0],
                    "pet_ids": pet_ids,
                })
    except Exception as exc:
        logger.error("Suggested questions: failed to query active users: %s", exc)
        return

    if not active_combos:
        logger.info("Suggested questions: no active users found")
        return

    logger.info("Suggested questions: processing %d user-pet combo(s)", len(active_combos))

    # ── 2. For each combo, check staleness and regenerate if needed ──────
    generated = 0
    skipped = 0
    failed = 0

    # Use the _MODEL constant from the agent module (same pattern as all other agents).
    # None = provider default from .env. Override _MODEL in the agent file to test models.
    from app.agents.suggested_questions import _MODEL as sq_model
    model = sq_model

    # Compute the 4-week history cutoff once (not per-iteration)
    history_cutoff_week = (datetime.now(timezone.utc) - timedelta(weeks=4)).strftime("%G-W%V")

    for combo in active_combos:
        user_code = combo["user_code"]
        pet_ids = combo["pet_ids"]

        try:
            # Resolve language from user record
            raw_user = await vk.get(CacheKeys.user(user_code))
            if raw_user:
                user_data = json.loads(raw_user)
                language = user_data.get("preferred_language", "JA")
                if language == "auto":
                    language = "JA"
            else:
                async with get_session() as db_session:
                    user_repo = UserRepo(db_session)
                    user_data = await user_repo.read(user_code)
                language = (user_data or {}).get("preferred_language", "JA")
                if language == "auto":
                    language = "JA"

            cache_key = CacheKeys.suggested_questions(user_code, pet_ids, language)

            # Check if cached questions exist and are fresh
            raw_cached = await vk.get(cache_key)
            if raw_cached:
                cached = json.loads(raw_cached)
                generated_at_str = cached.get("generated_at", "")

                # Get max(updated_at) from active_profile for staleness check
                # Use ORM query instead of raw SQL for asyncpg compatibility
                profile_last_updated_str: str | None = None
                try:
                    async with get_session() as db_session:
                        stmt = (
                            select(func.max(ActiveProfile.updated_at))
                            .where(
                                ActiveProfile.pet_id.in_(pet_ids),
                                ~ActiveProfile.field_key.startswith("_"),
                            )
                        )
                        result = await db_session.execute(stmt)
                        row = result.scalar()
                        if row:
                            profile_last_updated_str = str(row)
                except Exception as db_exc:
                    logger.debug("Suggested questions: staleness check DB error: %s", db_exc)

                # Compare as ISO strings (both are ISO-8601, lexicographic comparison is valid)
                if generated_at_str and profile_last_updated_str:
                    if profile_last_updated_str <= generated_at_str:
                        skipped += 1
                        continue
                elif generated_at_str and not profile_last_updated_str:
                    # No profile data at all, cached questions are fine
                    skipped += 1
                    continue

            # ── Generate questions ───────────────────────────────────────
            pet_profiles = []
            pet_contexts = []
            for pid in pet_ids:
                try:
                    pet_profile, aalda_facts = await pet_fetcher.fetch_pet_profile(user_code, pid)
                except PetFetchError:
                    raise

                pet_profiles.append(pet_profile)

                raw_profile = await vk.get(CacheKeys.profile(pid))
                if raw_profile:
                    active_raw = json.loads(raw_profile)
                else:
                    async with get_session() as db_session:
                        ap_repo = ActiveProfileRepo(db_session)
                        active_raw = await ap_repo.read_all(pid)

                ctx = build_pet_context(pet_profile, aalda_facts, active_raw)
                pet_contexts.append(ctx)

            # Build context for the prompt
            pets_json = json.dumps(
                [{"name": p.get("name", ""), "species": p.get("species", ""),
                  "breed": p.get("breed", ""), "age": c["active_profile"].get("age", {}).get("value", ""),
                  "sex": p.get("sex", "")}
                 for p, c in zip(pet_profiles, pet_contexts)],
                ensure_ascii=False,
            )

            # Trusted context: high-confidence facts only
            trusted = {}
            for c in pet_contexts:
                for key, entry in c["active_profile"].items():
                    if isinstance(entry, dict) and entry.get("confidence", 0) >= 0.6:
                        trusted[key] = entry.get("value", "")
            trusted_json = json.dumps(trusted, ensure_ascii=False)

            # Gather gap lists (deduplicated)
            all_gaps: list[str] = []
            for c in pet_contexts:
                all_gaps.extend(c.get("gap_list", []))
            gap_list = list(dict.fromkeys(all_gaps))

            # Load recent question history
            history_key = CacheKeys.suggested_history(user_code, pet_ids)
            raw_history = await vk.get(history_key)
            recent_questions: list[str] = []
            history_entries: list[dict] = []
            if raw_history:
                try:
                    history_entries = json.loads(raw_history)
                    for entry in history_entries:
                        recent_questions.extend(entry.get("questions", []))
                except (json.JSONDecodeError, TypeError):
                    pass

            # Generate
            questions = await sq_agent.generate(
                language=language,
                pet_count=len(pet_ids),
                pets_json=pets_json,
                trusted_context_json=trusted_json,
                gap_list=gap_list,
                recent_questions=recent_questions,
                model=model,
            )

            if not questions:
                questions = get_evergreen_questions(language, pet_count=len(pet_ids))
                logger.debug("Suggested questions: LLM failed, using evergreen for user=%s", user_code)

            # Validate
            all_passed, failed_indices, reasons = validate_questions(
                questions, language, len(pet_ids), recent_questions,
            )

            if not all_passed and questions[0].get("reason_type") != "evergreen":
                # Try one regeneration
                questions_retry = await sq_agent.generate(
                    language=language,
                    pet_count=len(pet_ids),
                    pets_json=pets_json,
                    trusted_context_json=trusted_json,
                    gap_list=gap_list,
                    recent_questions=recent_questions,
                    model=model,
                )
                if questions_retry:
                    passed2, _, _ = validate_questions(
                        questions_retry, language, len(pet_ids), recent_questions,
                    )
                    if passed2:
                        questions = questions_retry
                    else:
                        # Replace failed items with evergreen
                        evergreen = get_evergreen_questions(language, pet_count=len(pet_ids))
                        for idx in failed_indices:
                            if idx < len(evergreen):
                                questions[idx] = evergreen[idx]

            # Cache the result
            now_iso = datetime.now(timezone.utc).isoformat()
            cache_value = json.dumps({
                "generated_at": now_iso,
                "questions": questions,
            })
            await vk.setex(cache_key, jittered_ttl(TTL_SUGGESTED), cache_value)

            # Update history — prune entries older than 4 weeks
            current_week = datetime.now(timezone.utc).strftime("%G-W%V")
            history_entries = [
                e for e in history_entries
                if e.get("week", "") >= history_cutoff_week
            ]
            history_entries.append({
                "week": current_week,
                "questions": [q["text"] for q in questions],
            })
            await vk.setex(
                history_key,
                jittered_ttl(TTL_SUGGESTED_HISTORY),
                json.dumps(history_entries),
            )

            generated += 1
            logger.debug("Suggested questions: generated for user=%s pets=%s", user_code, pet_ids)

        except Exception as exc:
            failed += 1
            logger.error(
                "Suggested questions: failed for user=%s pets=%s error=%s",
                user_code, pet_ids, exc,
            )

    logger.info(
        "Suggested questions: generated=%d skipped=%d failed=%d total=%d",
        generated, skipped, failed, len(active_combos),
    )
