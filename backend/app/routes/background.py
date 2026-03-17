# app/routes/background.py
#
# Fire-and-forget background tasks extracted from chat.py.
#
# What lives here:
#   - _create_tracked_task()  — registers async tasks for graceful shutdown
#   - _run_background()       — Compressor + Aggregator pipeline, message persistence
#   - _run_compaction()       — LLM summarization when messages exceed threshold
#
# These run AFTER the HTTP response is sent — the user never waits.
# All exceptions are caught and logged, never propagated.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import dataclasses
from datetime import datetime, timezone
import logging
from typing import Any

# ── Our code ───────────────────────────────────────────────────────────────────
from app.agents.state import AgentState
from app.db.session import get_session
from app.db.repositories import (
    ActiveProfileRepo, FactLogRepo, ThreadRepo, ThreadMessageRepo,
)
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

        # ── Compaction trigger (guarded — W3) ─────────────────────────
        sessions = state_bag.sessions
        thread_messages = sessions.get(state.thread_id, [])
        compacting = state_bag.compaction_in_progress
        if (
            len(thread_messages) >= THREAD_COMPACTION_THRESHOLD
            and state.thread_id not in compacting
        ):
            compacting.add(state.thread_id)
            _create_tracked_task(_run_compaction(state.thread_id, state.pet_id, state_bag), state_bag)

        # ── Compressor pipeline ─────────────────────────────────────────
        if compressor is None:
            return

        facts = await compressor.run(state)

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

            for label, pet_facts in facts_by_pet.items():
                if label not in pet_id_map:
                    logger.warning("Unknown pet_label %r from Compressor — defaulting to Pet A (session=%s)", label, state.session_id)
                target_pet_id = pet_id_map.get(label, state.pets[0].id)
                to_log = [
                    {
                        **dataclasses.asdict(f),
                        "needs_clarification": f.confidence <= 0.70,
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                        "session_id": state.session_id,
                    }
                    for f in pet_facts
                ]
                async with get_session() as db_session:
                    repo = FactLogRepo(db_session)
                    await repo.append(to_log, pet_id=target_pet_id)

        logger.info(
            "Compressor done — session=%s extracted=%d high=%d low=%d dual=%s",
            state.session_id, len(facts), len(high), len(low), state.is_dual_pet,
        )

        # ── Aggregator — merge high-confidence facts per pet (parallel) ──
        if high and aggregator is not None:
            # Group high-confidence facts by pet_label
            high_by_pet: dict[str, list] = {}
            for f in high:
                high_by_pet.setdefault(f.pet_label, []).append(f)

            async def _aggregate_one_pet(label: str, pet_facts: list) -> None:
                target_pet_id = pet_id_map.get(label, state.pets[0].id)
                async with get_session() as db_session:
                    ap_repo = ActiveProfileRepo(db_session)
                    current_profile = await ap_repo.read_all(target_pet_id) or {}
                await aggregator.run(pet_facts, state.session_id, current_profile, pet_id=target_pet_id)

            await asyncio.gather(*[
                _aggregate_one_pet(label, pet_facts)
                for label, pet_facts in high_by_pet.items()
            ])

        # ── Persist low-confidence facts for clarification next turn ─────
        pending_store = getattr(state_bag, "pending_clarifications", {})
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
            existing = pending_store.get(state.thread_id, [])
            new_keys = {(c["pet_name"], c["key"]) for c in clarifications}
            kept = [p for p in existing if (p["pet_name"], p["key"]) not in new_keys]
            pending_store[state.thread_id] = kept + clarifications

            state.low_confidence_fields = clarifications
        else:
            # Clear pending clarifications if high-confidence facts resolved them
            existing = pending_store.get(state.thread_id, [])
            if existing and high:
                resolved_keys = set()
                for f in high:
                    pet_idx = 1 if f.pet_label == "pet_b" and state.is_dual_pet else 0
                    resolved_keys.add((state.pets[pet_idx].name, f.key))
                remaining = [p for p in existing if (p["pet_name"], p["key"]) not in resolved_keys]
                if remaining:
                    pending_store[state.thread_id] = remaining
                else:
                    pending_store.pop(state.thread_id, None)
            state.low_confidence_fields = []

    except Exception as exc:
        logger.error(
            "Background pipeline failed — session=%s error=%s",
            state.session_id, exc,
        )


async def _run_compaction(thread_id: str, pet_id: int, state_bag: StateBag) -> None:
    """
    Fire-and-forget compaction task.

    When message count exceeds THREAD_COMPACTION_THRESHOLD, summarize older
    messages with an LLM, store the summary in threads.compaction_summary,
    and trim the in-memory list to THREAD_CONTEXT_WINDOW recent messages.
    """
    try:
        sessions = state_bag.sessions
        # Snapshot the list so mutations during LLM call don't affect us (Addendum)
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
            current = sessions.get(thread_id, [])
            # Keep the last THREAD_CONTEXT_WINDOW messages from the CURRENT list
            sessions[thread_id] = current[-THREAD_CONTEXT_WINDOW:]
            trimmed = len(current) - THREAD_CONTEXT_WINDOW

        logger.info(
            "Compaction done — thread=%s summarized=%d trimmed=%d kept=%d",
            thread_id, len(old_messages), trimmed, THREAD_CONTEXT_WINDOW,
        )

    except Exception as exc:
        logger.error("Compaction failed — thread=%s error=%s", thread_id, exc)
    finally:
        state_bag.compaction_in_progress.discard(thread_id)
