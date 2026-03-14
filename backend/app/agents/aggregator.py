# app/agents/aggregator.py
#
# Agent 3 — the Aggregator.
#
# Runs fire-and-forget AFTER the Compressor inside _run_background().
# Receives high-confidence facts (> 0.70) and merges them into active_profile.
# Never talks to the user. Never calls an LLM. Pure deterministic logic.
#
# How this file is organised:
#   1. AggregatorAgent       — the agent class with run() and _apply_rules()
#   2. _build_entry          — converts an ExtractedFact into an active_profile entry dict
#   3. _normalize_confidence — handles legacy seed data integers (80 -> 0.80)
#
# Storage:
#   - Constructor accepts get_session (async context manager factory) for DB writes.
#   - run() writes through to PostgreSQL via ActiveProfileRepo.
#   - _apply_rules() is COMPLETELY UNCHANGED — Rules 0-6 untouched.
#
# Design decisions (see design-docs/aggregator-design.md for full rationale):
#   - Rules 0-6 applied per fact in priority order. First matching rule wins.
#   - updated_at uses datetime.now(UTC) at merge time — not fact.timestamp.
#   - asyncio.Lock prevents concurrent read-modify-write races from rapid messages.
#   - Keys starting with "_" (like _pet_history) are skipped — they are metadata.
#   - On any failure: caller (_run_background) catches and logs — never crashes.

import asyncio
import logging
from datetime import datetime, timezone

from app.agents.compressor import ExtractedFact
from app.db.repositories import ActiveProfileRepo
from constants import DEFAULT_PET_ID

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize_confidence(value: float | int) -> float:
    """
    Ensure confidence is a 0.0–1.0 float.

    Seed data uses integers (80, 90). The Compressor outputs floats (0.80, 0.90).
    Normalize so comparisons are always on the same scale.
    """
    if isinstance(value, int) or value > 1.0:
        return value / 100.0
    return float(value)


def _build_entry(
    fact: ExtractedFact,
    session_id: str,
    status: str,
    change_detected: str = "",
) -> dict:
    """
    Convert an ExtractedFact into the dict shape stored in active_profile.json.

    Matches the ActiveProfileEntry dataclass fields (minus `key`, which is the
    dict key in active_profile).
    """
    return {
        "value": fact.value,
        "confidence": fact.confidence,
        "source_rank": fact.source_rank,
        "time_scope": fact.time_scope,
        "source_quote": fact.source_quote,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "status": status,
        "change_detected": change_detected,
        "trend_flag": "",  # Phase B
    }


# ── AggregatorAgent ───────────────────────────────────────────────────────────

class AggregatorAgent:
    """
    Pure rule-based fact aggregator. No LLM.

    Merges high-confidence facts from the Compressor into active_profile
    (in-memory dict on app.state, written through to PostgreSQL).
    Each fact is compared against the current entry for that key. The first
    matching rule wins — remaining rules are not evaluated.
    """

    def __init__(self, get_session=None) -> None:
        self._lock = asyncio.Lock()
        self._get_session = get_session  # async context manager for DB writes
        logger.info("AggregatorAgent initialised (no LLM).")

    async def run(
        self,
        facts: list[ExtractedFact],
        session_id: str,
        active_profile: dict | None = None,
    ) -> dict:
        """
        Merge a list of high-confidence facts into active_profile.

        Mutates active_profile in place (app.state reference) and writes
        through to PostgreSQL for persistence.

        Acquires an asyncio lock around the entire read-apply-write cycle
        to prevent concurrent background tasks from racing.

        Args:
            facts: High-confidence ExtractedFact objects (confidence > 0.70).
            session_id: Which conversation produced these facts.
            active_profile: In-memory profile dict (from app.state).

        Returns:
            The updated active_profile dict after all merges.
        """
        async with self._lock:
            if active_profile is None:
                logger.warning(
                    "Aggregator called without active_profile — using empty dict.")
            profile = active_profile if active_profile is not None else {}
            changes = 0

            for fact in facts:
                changed = self._apply_rules(fact, profile, session_id)
                if changed:
                    changes += 1

            if changes > 0 and self._get_session is not None:
                async with self._get_session() as session:
                    repo = ActiveProfileRepo(session)
                    await repo.write_all(DEFAULT_PET_ID, profile)

            logger.info(
                "Aggregator done — session=%s facts=%d changes=%d",
                session_id, len(facts), changes,
            )
            return profile

    def _apply_rules(
        self,
        fact: ExtractedFact,
        profile: dict,
        session_id: str,
    ) -> bool:
        """
        Apply conflict resolution Rules 0–6 to a single fact.

        Mutates `profile` dict in place. Returns True if profile was changed.
        First matching rule wins — remaining rules are not evaluated.
        """
        key = fact.key

        # Skip metadata keys (e.g., _pet_history).
        if key.startswith("_"):
            return False

        # ── Rule 0: time_scope gate ────────────────────────────────────────
        # Past facts belong in fact_log only, not in the current profile.
        if fact.time_scope == "past":
            logger.debug(
                "Rule 0 — skip past fact: key=%s value=%r", key, fact.value)
            return False

        current = profile.get(key)

        # ── Rule 1: First-time key ─────────────────────────────────────────
        if current is None:
            profile[key] = _build_entry(fact, session_id, status="new")
            logger.debug("Rule 1 — new key: %s = %r (conf=%.2f)",
                         key, fact.value, fact.confidence)
            return True

        # From here, `current` is an existing entry dict.
        current_conf = _normalize_confidence(current.get("confidence", 0))

        # ── Rule 2: User explicit correction ───────────────────────────────
        if fact.source_rank == "user_correction":
            old_value = current.get("value", "")
            change = f"{old_value} → {fact.value}" if old_value != fact.value else ""
            profile[key] = _build_entry(
                fact, session_id, status="updated", change_detected=change)
            logger.info(
                "Rule 2 — user correction: %s %r → %r (conf=%.2f)",
                key, old_value, fact.value, fact.confidence,
            )
            return True

        # ── Rule 3: Confirmation (same value, same key) ───────────────────
        if fact.value == current.get("value"):
            boosted = min(current_conf + 0.05, 1.0)
            current["confidence"] = boosted
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            current["status"] = "confirmed"
            current["session_id"] = session_id
            logger.debug(
                "Rule 3 — confirmation: %s = %r (conf %.2f → %.2f)",
                key, fact.value, current_conf, boosted,
            )
            return True

        # ── Rule 4: Low-confidence new fact ────────────────────────────────
        threshold = current_conf * 0.80
        if fact.confidence < threshold:
            logger.debug(
                "Rule 4 — skip low-confidence: %s new=%.2f < threshold=%.2f (current=%.2f)",
                key, fact.confidence, threshold, current_conf,
            )
            return False

        # ── Rule 5: New fact is better ─────────────────────────────────────
        if fact.confidence >= threshold:
            old_value = current.get("value", "")
            change = f"{old_value} → {fact.value}" if old_value != fact.value else ""
            profile[key] = _build_entry(
                fact, session_id, status="updated", change_detected=change)
            logger.info(
                "Rule 5 — better fact: %s %r → %r (conf %.2f → %.2f)",
                key, old_value, fact.value, current_conf, fact.confidence,
            )
            return True

        # ── Rule 6: True conflict (should rarely reach here) ──────────────
        logger.warning(
            "Rule 6 — true conflict (keeping current): key=%s current=%r (%.2f) vs new=%r (%.2f)",
            key, current.get(
                "value"), current_conf, fact.value, fact.confidence,
        )
        return False
