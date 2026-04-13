# app/routes/background.py
#
# Fire-and-forget background tasks extracted from chat.py.
#
# What lives here:
#   - _create_tracked_task()          — registers async tasks for graceful shutdown
#   - _run_background()               — Compressor + Aggregator pipeline, message persistence
#   - _run_compaction()               — LLM summarization when messages exceed threshold
#   - _maybe_run_history_builder()    — HistoryBuilder hybrid trigger (ft-013)
#
# These run AFTER the HTTP response is sent — the user never waits.
# All exceptions are caught and logged, never propagated.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import dataclasses
from datetime import datetime, timezone
import json
import logging
from typing import Any
from uuid import uuid4

# ── Retry policy for the compressor/aggregator pipeline (T2-04) ───────────────
#
# Message persistence (DB write of user+assistant messages) runs ONCE — no retry
# because retrying would write duplicate rows.
#
# The compressor + aggregator + fact logging section is retried up to 3 times:
#   attempt 1 — immediate
#   attempt 2 — after 3 seconds
#   attempt 3 — after 10 seconds
#
# These delays cover transient DB blips (usually resolve in <5s) without holding
# the background task alive for too long.  After 3 failures the error is logged
# and the pipeline gives up — messages are already saved, only fact extraction
# is missed for this turn.
_PIPELINE_RETRY_DELAYS: list[int] = [3, 10]  # delays BETWEEN attempts

# ── Our code ───────────────────────────────────────────────────────────────────
from app.agents.state import AgentState
from app.cache.keys import (
    CacheKeys, LUA_APPEND_MESSAGES, LUA_RELEASE_LOCK,
    TTL_COMPACTING, TTL_PENDING, TTL_SESSION, jittered_ttl,
)
from app.db.session import get_session
from app.db.repositories import (
    ActiveProfileRepo, FactLogRepo, SuggestedQuestionsRepo, ThreadRepo, ThreadMessageRepo, UserRepo,
)
from app.services.question_generation.generator import regen_for_user
from app.types import StateBag
from constants import THREAD_COMPACTION_THRESHOLD, THREAD_CONTEXT_WINDOW

logger = logging.getLogger(__name__)


# ── Task tracking (W8 — graceful shutdown) ────────────────────────────────────

def _create_tracked_task(coro: Any, state_bag: StateBag) -> asyncio.Task:
    """Create an asyncio task and register it for graceful shutdown tracking."""
    task = asyncio.create_task(coro)
    state_bag.background_tasks.add(task)
    task.add_done_callback(state_bag.background_tasks.discard)
    return task


# ── Background pipeline ──────────────────────────────────────────────────────

async def _run_background(state: AgentState, state_bag: StateBag) -> None:
    """
    Fire-and-forget coroutine launched by asyncio.create_task() after /chat returns.

    Three responsibilities:
      1. Write-through: persist user + assistant messages to thread_messages table.
      2. Compaction trigger: if message count >= threshold, fire _run_compaction().
      3. Compressor + Aggregator: extract facts, split by confidence, persist to
         fact_log, then merge high-confidence facts into active_profile.

    User never waits for any of this. Never raises — exceptions caught and logged.
    """
    compressor = state_bag.compressor
    aggregator = state_bag.aggregator
    vk = getattr(state_bag, "valkey", None)

    try:
        # ── Write-through: persist messages to PostgreSQL ────────────────
        now_iso = datetime.now(timezone.utc).isoformat()
        async with get_session() as db_session:
            msg_repo = ThreadMessageRepo(db_session)
            await msg_repo.append_batch([
                {
                    "thread_id": state.thread_id,
                    "role": "user",
                    "content": state.user_message,
                    "timestamp": now_iso,
                },
                {
                    "thread_id": state.thread_id,
                    "role": "assistant",
                    "content": state.agent_reply,
                    "timestamp": now_iso,
                },
            ])

        # ── Valkey session append — after DB write succeeds (ft-005) ─────────
        # DB is written first (source of truth). Now append the same two messages
        # to Valkey atomically using the Lua script. If Valkey is down, vk is None
        # and this is skipped — the local sessions dict still has the data.
        if vk is not None:
            session_msgs = [
                {"role": "user", "content": state.user_message, "timestamp": now_iso},
                {"role": "assistant", "content": state.agent_reply, "timestamp": now_iso},
            ]
            await vk.eval(
                LUA_APPEND_MESSAGES, 1,
                CacheKeys.session(state.thread_id),
                json.dumps(session_msgs),
                str(jittered_ttl(TTL_SESSION)),
            )

        # ── Compaction trigger — Valkey distributed lock (ft-005, Step 8) ──
        # Use Valkey SETNX (set if not exists) as an atomic distributed lock
        # so multiple instances don't compact the same thread simultaneously.
        # Fallback: if Valkey is down, use local compaction_in_progress set.
        thread_messages = state.recent_history  # use the snapshot from AgentState
        if len(thread_messages) >= THREAD_COMPACTION_THRESHOLD:
            should_compact = False
            if vk is not None:
                lock_token = str(uuid4())
                acquired = await vk.set(
                    CacheKeys.compacting(state.thread_id), lock_token,
                    nx=True, ex=TTL_COMPACTING,
                )
                should_compact = bool(acquired)
            else:
                # Valkey unavailable — fall back to local set (single-instance safety)
                compacting = state_bag.compaction_in_progress
                if state.thread_id not in compacting:
                    compacting.add(state.thread_id)
                    should_compact = True

            if should_compact:
                _create_tracked_task(
                    _run_compaction(state.thread_id, state_bag, lock_token if vk is not None else ""),
                    state_bag,
                )

        # ── Compressor pipeline (with retry) ────────────────────────────
        # Message persistence above runs once. From here on we retry — a
        # transient DB blip or LLM error should not silently drop facts.
        if compressor is None:
            return

        facts = None
        last_exc: Exception | None = None
        for attempt, delay in enumerate([0] + _PIPELINE_RETRY_DELAYS, start=1):
            if delay:
                logger.warning(
                    "Background pipeline retry %d — session=%s sleeping %ds",
                    attempt, state.session_id, delay,
                )
                await asyncio.sleep(delay)
            try:
                facts = await compressor.run(state)
                break  # success — exit retry loop
            except Exception as exc:
                last_exc = exc
                logger.error(
                    "Background pipeline attempt %d failed — session=%s error=%s%s",
                    attempt, state.session_id, exc,
                    " — retrying" if attempt <= len(_PIPELINE_RETRY_DELAYS) else " — giving up",
                )

        if facts is None:
            logger.error(
                "Background pipeline gave up after %d attempts — session=%s last_error=%s",
                len(_PIPELINE_RETRY_DELAYS) + 1, state.session_id, last_exc,
            )
            return

        high = [f for f in facts if f.confidence > 0.70]
        low  = [f for f in facts if 0.50 <= f.confidence <= 0.70]

        state.extracted_facts = high

        # ── Build pet_id lookup from state.pets ──────────────────────────
        pet_id_map = {"pet_a": state.pets[0].id}
        if state.is_dual_pet:
            pet_id_map["pet_b"] = state.pets[1].id

        # ── Log facts to correct pet (split by pet_label) ───────────────
        if facts:
            # Group all facts by pet_label
            facts_by_pet: dict[str, list] = {}
            for f in facts:
                facts_by_pet.setdefault(f.pet_label, []).append(f)

            # Build all (facts_list, pet_id) pairs then write atomically in one commit.
            # append_bulk() does a single add_all + commit across all pets so a failure
            # on Pet B cannot leave Pet A's facts committed while Pet B's are silently lost.
            extracted_at = datetime.now(timezone.utc).isoformat()
            all_to_log: list[tuple[list, int]] = []
            for label, pet_facts in facts_by_pet.items():
                if label not in pet_id_map:
                    logger.warning("Unknown pet_label %r from Compressor — defaulting to Pet A (session=%s)", label, state.session_id)
                target_pet_id = pet_id_map.get(label, state.pets[0].id)
                to_log = [
                    {
                        **dataclasses.asdict(f),
                        "needs_clarification": f.confidence <= 0.70,
                        "extracted_at": extracted_at,
                        "session_id": state.session_id,
                    }
                    for f in pet_facts
                ]
                all_to_log.append((to_log, target_pet_id))

            async with get_session() as db_session:
                repo = FactLogRepo(db_session)
                await repo.append_bulk(all_to_log, user_code=state.user_code)

        logger.info(
            "Compressor done — session=%s extracted=%d high=%d low=%d dual=%s",
            state.session_id, len(facts), len(high), len(low), state.is_dual_pet,
        )

        # ── Aggregator — merge high-confidence facts per pet (parallel) ──
        # high_by_pet is built unconditionally so the HistoryBuilder block below
        # can reference it even when aggregator is None.
        high_by_pet: dict[str, list] = {}
        for f in high:
            high_by_pet.setdefault(f.pet_label, []).append(f)

        if high and aggregator is not None:

            async def _aggregate_one_pet(label: str, pet_facts: list) -> None:
                target_pet_id = pet_id_map.get(label, state.pets[0].id)
                # Read current profile — Valkey cache-aside (ft-005, Step 6)
                vk_inner = getattr(state_bag, "valkey", None)
                current_profile: dict = {}
                if vk_inner is not None:
                    raw_prof = await vk_inner.get(CacheKeys.profile(target_pet_id))
                    if raw_prof is not None:
                        current_profile = json.loads(raw_prof)
                if not current_profile:
                    async with get_session() as db_session:
                        ap_repo = ActiveProfileRepo(db_session)
                        current_profile = await ap_repo.read_all(target_pet_id) or {}
                await aggregator.run(pet_facts, state.session_id, current_profile, pet_id=target_pet_id, user_code=state.user_code)

            await asyncio.gather(*[
                _aggregate_one_pet(label, pet_facts)
                for label, pet_facts in high_by_pet.items()
            ])

            # ── Immediately regenerate suggested questions (profile just changed) ──
            # Aggregator already busted the cache above. Fill it now so the user
            # sees fresh personalized questions on their very next /setup call —
            # without waiting for the nightly job at midnight.
            _create_tracked_task(
                _regen_suggested_questions(
                    state.user_code, [p.id for p in state.pets], state_bag,
                ),
                state_bag,
            )

        # ── HistoryBuilder — rebuild pet history narrative (ft-013) ──────
        # Runs after Aggregator so both pipelines use the same high-confidence facts.
        # Each pet wrapped in its own try/except — a HistoryBuilder failure must NOT
        # propagate to the outer except and skip the low-confidence persistence below.
        if high:
            for label, pet_facts in high_by_pet.items():
                target_pet_id = pet_id_map.get(label, state.pets[0].id)
                try:
                    await _maybe_run_history_builder(target_pet_id, pet_facts, state_bag, user_code=state.user_code)
                except Exception as hb_exc:
                    logger.error(
                        "HistoryBuilder failed — pet_id=%s session=%s error=%s",
                        target_pet_id, state.session_id, hb_exc,
                    )

        # ── Persist low-confidence facts for clarification next turn ─────
        # (ft-005, Step 5): Write to Valkey; fall back to local dict when Valkey down.
        pending_store = getattr(state_bag, "pending_clarifications", {})
        pending_key = CacheKeys.pending(state.thread_id)

        if low:
            clarifications = []
            for f in low:
                pet_idx = 1 if f.pet_label == "pet_b" and state.is_dual_pet else 0
                clarifications.append({
                    "pet_name": state.pets[pet_idx].name,
                    "key": f.key,
                    "value": f.value,
                    "source_quote": f.source_quote,
                })
            # Deduplicate by (pet_name, key) — newer value replaces older
            # Read existing from Valkey (may be None if first time)
            existing: list = []
            if vk is not None:
                raw_existing = await vk.get(pending_key)
                if raw_existing is not None:
                    existing = json.loads(raw_existing)
            else:
                existing = pending_store.get(state.thread_id, [])

            new_keys = {(c["pet_name"], c["key"]) for c in clarifications}
            kept = [p for p in existing if (p["pet_name"], p["key"]) not in new_keys]
            final_pending = kept + clarifications

            # Write to Valkey (primary) + local dict (fallback)
            if vk is not None:
                await vk.setex(pending_key, jittered_ttl(TTL_PENDING), json.dumps(final_pending))
            pending_store[state.thread_id] = final_pending
            state.low_confidence_fields = clarifications
        else:
            # Clear pending clarifications if high-confidence facts resolved them
            existing = []
            if vk is not None:
                raw_existing = await vk.get(pending_key)
                if raw_existing is not None:
                    existing = json.loads(raw_existing)
            else:
                existing = pending_store.get(state.thread_id, [])

            if existing and high:
                resolved_keys = set()
                for f in high:
                    pet_idx = 1 if f.pet_label == "pet_b" and state.is_dual_pet else 0
                    resolved_keys.add((state.pets[pet_idx].name, f.key))
                remaining = [p for p in existing if (p["pet_name"], p["key"]) not in resolved_keys]
                if remaining:
                    if vk is not None:
                        await vk.setex(pending_key, jittered_ttl(TTL_PENDING), json.dumps(remaining))
                    pending_store[state.thread_id] = remaining
                else:
                    if vk is not None:
                        await vk.delete(pending_key)
                    pending_store.pop(state.thread_id, None)
            state.low_confidence_fields = []

    except Exception as exc:
        logger.error(
            "Background pipeline failed — session=%s error=%s",
            state.session_id, exc,
        )


async def _regen_suggested_questions(
    user_code: str,
    pet_ids: list[int],
    state_bag: StateBag,
) -> None:
    """
    Immediately regenerate suggested questions after the aggregator updates the profile.

    Called fire-and-forget from _run_background when high-confidence facts are merged.
    The aggregator has already busted the suggested-questions cache (deleted the keys).
    This fills them back with fresh personalized questions so the user sees them on their
    very next /setup call — without waiting until the nightly job at midnight.

    Regens each pet separately (per-pet design): one regen_for_user() call per pet_id.
    """
    sq_agent = getattr(state_bag, "suggested_questions_agent", None)
    vk = getattr(state_bag, "valkey", None)
    pet_fetcher = getattr(state_bag, "pet_fetcher", None)

    if sq_agent is None or vk is None or pet_fetcher is None:
        logger.debug("_regen_suggested_questions: required services unavailable, skipping")
        return

    # Resolve language: Valkey first, DB fallback if cache is cold
    language = "JA"
    raw_user = await vk.get(CacheKeys.user(user_code))
    if raw_user:
        try:
            user_data = json.loads(raw_user)
            lang = user_data.get("preferred_language", "JA")
            language = lang if lang != "auto" else "JA"
        except (json.JSONDecodeError, TypeError):
            pass
    else:
        # Valkey cold — load preferred_language from DB to avoid serving the wrong language
        try:
            async with get_session() as db_session:
                user_repo = UserRepo(db_session)
                user_record = await user_repo.read(user_code)
            if user_record:
                lang = user_record.get("preferred_language", "JA")
                language = lang if lang != "auto" else "JA"
        except Exception:
            pass  # last resort: keep default "JA"

    # Regen each pet separately — each gets its own 10-question set
    for i, pid in enumerate(pet_ids):
        async with get_session() as db_session:
            sq_repo = SuggestedQuestionsRepo(db_session)
            await regen_for_user(
                user_code=user_code,
                pet_id=pid,
                language=language,
                suggested_agent=sq_agent,
                suggested_repo=sq_repo,
                valkey=vk,
                aalda_client=pet_fetcher,
                db_session=db_session,
                is_pet_b=(i == 1),
            )


async def _maybe_run_history_builder(
    pet_id: int,
    new_high_facts: list,
    state_bag: StateBag,
    user_code: str = "",
) -> None:
    """
    Hybrid trigger for HistoryBuilder (ft-013).

    Runs HistoryBuilder if EITHER condition is met:
      A) >= 3 new high-confidence facts in this session (significant new info)
      B) >= 1 new fact AND >= 2 distinct sessions have passed since last build
         (gradual accumulation across sessions)

    After build, writes new narrative to active_profile and invalidates
    the Valkey profile cache so the next request sees fresh _pet_history.
    """
    history_builder = getattr(state_bag, "history_builder", None)
    if history_builder is None:
        return

    new_facts_count = len(new_high_facts)
    if new_facts_count == 0:
        return

    # ── Read _history_last_updated from active_profile ───────────────────
    async with get_session() as db_session:
        ap_repo = ActiveProfileRepo(db_session)
        profile = await ap_repo.read_all(pet_id) or {}

    last_updated = profile.get("_history_last_updated", "") or ""
    existing_history = profile.get("_pet_history", "") or ""
    if isinstance(existing_history, dict):
        # _pet_history stored as dict entry in old format — extract value
        existing_history = existing_history.get("value", "") or ""

    # ── Count sessions since last build ──────────────────────────────────
    sessions_since_build = 0
    if last_updated:
        async with get_session() as db_session:
            fact_repo = FactLogRepo(db_session)
            sessions_since_build = await fact_repo.count_distinct_sessions_since(
                pet_id, last_updated,
            )

    # ── Hybrid trigger logic ─────────────────────────────────────────────
    should_run = (
        new_facts_count >= 3                                        # Condition A
        or (new_facts_count >= 1 and sessions_since_build >= 2)    # Condition B
    )
    if not should_run:
        logger.debug(
            "HistoryBuilder skipped — pet_id=%s new_facts=%d sessions_since=%d",
            pet_id, new_facts_count, sessions_since_build,
        )
        return

    # ── Fetch all new facts from fact_log since last build ───────────────
    since = last_updated or "1970-01-01T00:00:00+00:00"
    async with get_session() as db_session:
        fact_repo = FactLogRepo(db_session)
        all_new_facts = await fact_repo.read_since(pet_id, since)

    # ── Build updated history narrative ──────────────────────────────────
    new_history = await history_builder.build(pet_id, all_new_facts, existing_history)

    # Only persist if the narrative actually changed.
    # build() returns existing_history unchanged when no health-relevant facts were found.
    # Advancing _history_last_updated when nothing changed would permanently skip those
    # facts on the next run — they'd fall before the new last_updated pointer.
    if new_history == existing_history:
        logger.debug(
            "HistoryBuilder: narrative unchanged — skipping write (pet_id=%s)", pet_id,
        )
        return

    # ── Persist to active_profile ─────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    async with get_session() as db_session:
        ap_repo = ActiveProfileRepo(db_session)
        await ap_repo.write_history(pet_id, new_history, now_iso, user_code=user_code)

    # ── Invalidate Valkey profile cache ───────────────────────────────────
    # Next request will miss the cache and reload fresh _pet_history from DB.
    vk = getattr(state_bag, "valkey", None)
    if vk is not None:
        await vk.delete(CacheKeys.profile(pet_id))

    logger.info(
        "HistoryBuilder complete — pet_id=%s new_facts=%d sessions_since_last=%d",
        pet_id, len(all_new_facts), sessions_since_build,
    )


async def _run_compaction(thread_id: str, state_bag: StateBag, lock_token: str = "") -> None:
    """
    Fire-and-forget compaction task.

    When message count exceeds THREAD_COMPACTION_THRESHOLD, summarize older
    messages with an LLM, store the summary in threads.compaction_summary,
    and trim the in-memory + Valkey session list to THREAD_CONTEXT_WINDOW.

    The compaction lock is held in Valkey (SETNX, 5-min TTL) for cross-instance
    safety.  Released in the finally block.  If Valkey is down, falls back to
    app.state.compaction_in_progress (single-instance fallback).
    """
    vk = getattr(state_bag, "valkey", None)
    lock_key = CacheKeys.compacting(thread_id)

    try:
        sessions = state_bag.sessions
        # Load current session from Valkey for a consistent snapshot
        messages: list = []
        if vk is not None:
            raw = await vk.get(CacheKeys.session(thread_id))
            if raw is not None:
                messages = json.loads(raw)
        if not messages:
            # Fallback to local sessions dict
            messages = list(sessions.get(thread_id, []))

        if len(messages) < THREAD_COMPACTION_THRESHOLD:
            return

        old_messages = messages[:-THREAD_CONTEXT_WINDOW]

        async with get_session() as db_session:
            thread_repo = ThreadRepo(db_session)
            thread = await thread_repo.get_by_thread_id(thread_id)
            existing_summary = thread.get("compaction_summary") if thread else None

        summarizer = state_bag.thread_summarizer
        new_summary = await summarizer.summarize(old_messages, existing_summary)

        # Find the DB cutoff ID — messages up to this ID are now summarized (W12)
        async with get_session() as db_session:
            msg_repo = ThreadMessageRepo(db_session)
            cutoff_id = await msg_repo.get_compaction_cutoff_id(
                thread_id, THREAD_CONTEXT_WINDOW,
            )

        async with get_session() as db_session:
            thread_repo = ThreadRepo(db_session)
            await thread_repo.update_compaction_summary(
                thread_id, new_summary, compacted_before_id=cutoff_id,
            )

        # Acquire per-thread lock before replacing the session list (C2).
        # Re-trim from the CURRENT list so messages appended during the
        # LLM summarization call are not lost.
        thread_lock = state_bag.thread_locks.setdefault(thread_id, asyncio.Lock())
        async with thread_lock:
            # Re-read current session (may have grown during LLM call)
            current: list = []
            if vk is not None:
                raw_current = await vk.get(CacheKeys.session(thread_id))
                if raw_current is not None:
                    current = json.loads(raw_current)
            if not current:
                current = sessions.get(thread_id, [])

            trimmed_list = current[-THREAD_CONTEXT_WINDOW:]
            trimmed = max(0, len(current) - THREAD_CONTEXT_WINDOW)

            # Write trimmed session back to Valkey (primary) + local dict (fallback)
            if vk is not None:
                await vk.setex(
                    CacheKeys.session(thread_id),
                    jittered_ttl(TTL_SESSION),
                    json.dumps(trimmed_list),
                )
            sessions[thread_id] = trimmed_list

        logger.info(
            "Compaction done — thread=%s summarized=%d trimmed=%d kept=%d",
            thread_id, len(old_messages), trimmed, THREAD_CONTEXT_WINDOW,
        )

    except Exception as exc:
        logger.error("Compaction failed — thread=%s error=%s", thread_id, exc)
    finally:
        # Release the compaction lock — token-checked so a slow compaction cannot
        # delete a lock acquired by another instance after our TTL expired.
        if vk is not None:
            if lock_token:
                await vk.eval(LUA_RELEASE_LOCK, 1, lock_key, lock_token)
            else:
                await vk.delete(lock_key)  # fallback: no token (Valkey was down at acquire time)
        else:
            state_bag.compaction_in_progress.discard(thread_id)
