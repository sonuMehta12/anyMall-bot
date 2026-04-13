# app/services/question_generation/generator.py
#
# Per-pet orchestration service for generating 10 suggested questions per pet.
#
# Public API:
#   regen_for_user(user_code, pet_id, language, sq_agent, sq_repo, valkey,
#                  aalda_client, db_session, is_pet_b) -> None
#
# Used by:
#   - background._regen_suggested_questions  (loops per pet_id)
#   - nightly._pregenerate_suggested_questions (iterates per-pet rows)
#
# Design:
#   - One LLM call per pet per regen (not shared across pets)
#   - Each pet has its own 10-question set: 3 dedicated + 1 both per module
#   - Validates each slot with validate_slot()
#   - Patches failed slots with evergreen (never crashes on partial failure)
#   - Writes to Valkey (hot cache, keyed by pet_id) and Postgres (cold storage)
#   - Updates 4-week history per user (anti-repeat applies across all pets)

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.suggested_questions import SuggestedQuestionsAgent, _MODEL as _SQ_MODEL
from app.cache.keys import CacheKeys, TTL_SUGGESTED, TTL_SUGGESTED_HISTORY, jittered_ttl
from app.db.repositories import ActiveProfileRepo, SuggestedQuestionsRepo
from app.services.context_builder import build_pet_context
from app.services.question_generation.templates import get_evergreen_questions
from app.services.question_generation.validator import validate_slot

logger = logging.getLogger(__name__)


async def regen_for_user(
    user_code: str,
    pet_id: int,
    language: str,
    suggested_agent: SuggestedQuestionsAgent,
    suggested_repo: SuggestedQuestionsRepo,
    valkey: Any,
    aalda_client: Any,
    db_session: Any,
    is_pet_b: bool = False,
) -> None:
    """
    Generate 10 fresh suggested questions for ONE pet and persist them.

    Steps:
      1. Fetch pet profile from AALDA for this pet_id
      2. Build pets_json, trusted_context_json, gap_list
      3. Load 4-week question history from Valkey (shared across all pets)
      4. Call SuggestedQuestionsAgent.generate() → 10 questions
      5. If empty → fill all 10 slots from evergreen pools
      6. Validate each slot with validate_slot()
      7. Patch any failed slot with get_evergreen_questions()
      8. Write to Postgres first (cold storage, source of truth)
      9. Write to Valkey (hot cache) — only after DB succeeds
     10. Append all 10 question texts to 4-week history (not just the 3 served by
         /setup — storing all 10 prevents the LLM regenerating the same questions
         next cycle, even for questions the user never saw)

    Args:
        is_pet_b: True if this pet is the second pet in the user's list.
                  Determines target label ("pet_b" vs "pet_a") in generated questions.

    Never raises — all exceptions are caught and logged.
    Caller can fire-and-forget safely.
    """
    try:
        # ── 1 & 2: Build pet context for this single pet ─────────────────────
        pet_profile, aalda_facts = await aalda_client.fetch_pet_profile(user_code, pet_id)

        # Read active profile: try Valkey first, fall back to DB
        raw_profile = await valkey.get(CacheKeys.profile(pet_id))
        if raw_profile:
            active_raw = json.loads(raw_profile)
        else:
            ap_repo = ActiveProfileRepo(db_session)
            active_raw = await ap_repo.read_all(pet_id)

        ctx = build_pet_context(pet_profile, aalda_facts, active_raw)

        pets_json = json.dumps(
            [
                {
                    "name": pet_profile.get("name", ""),
                    "species": pet_profile.get("species", ""),
                    "breed": pet_profile.get("breed", ""),
                    "age": ctx["active_profile"].get("age", {}).get("value", ""),
                    "sex": pet_profile.get("sex", ""),
                }
            ],
            ensure_ascii=False,
        )

        trusted: dict = {}
        for key, entry in ctx["active_profile"].items():
            if isinstance(entry, dict) and entry.get("confidence", 0) >= 0.6:
                trusted[key] = entry.get("value", "")
        trusted_json = json.dumps(trusted, ensure_ascii=False)
        gap_list = ctx.get("gap_list", [])

        # ── 3: Load 4-week history (shared across all pets for this user) ────
        history_key = CacheKeys.suggested_history(user_code, language)
        raw_history = await valkey.get(history_key)
        recent_questions: list[str] = []
        history_entries: list[dict] = []
        if raw_history:
            try:
                history_entries = json.loads(raw_history)
                for entry in history_entries:
                    recent_questions.extend(entry.get("questions", []))
            except (json.JSONDecodeError, TypeError):
                pass

        # ── 4: Generate 10 questions for this pet ────────────────────────────
        questions = await suggested_agent.generate(
            language=language,
            pet_id=pet_id,
            pets_json=pets_json,
            trusted_context_json=trusted_json,
            gap_list=gap_list,
            recent_questions=recent_questions,
            model=_SQ_MODEL,
            is_pet_b=is_pet_b,
        )

        # ── 5: If LLM returned nothing → fill all 10 with evergreen ─────────
        if not questions:
            logger.warning(
                "regen_for_user: LLM returned empty — filling all 10 slots with evergreen "
                "(user=%s pet_id=%s lang=%s)", user_code, pet_id, language,
            )
            questions = _build_full_evergreen(language, is_pet_b=is_pet_b)

        # ── 6 & 7: Per-slot validation + patch ──────────────────────────────
        pet_count = 1  # generating for one pet; single-pet guard applies
        final: list[dict] = list(questions)  # copy
        for i, q in enumerate(questions):
            reason = validate_slot(
                question=q,
                slot_index=i,
                all_questions=final,
                language=language,
                pet_count=pet_count,
                history=recent_questions,
            )
            if reason:
                logger.debug(
                    "regen_for_user: slot %d failed validation (%s) — patching with evergreen "
                    "(user=%s pet_id=%s lang=%s text=%r)",
                    i, reason, user_code, pet_id, language, q.get("text", ""),
                )
                module = q.get("module", "anymall")
                target = q.get("target", "pet_a" if not is_pet_b else "pet_b")
                patch = get_evergreen_questions(module, language, target, count=1)
                if patch:
                    final[i] = patch[0]
                else:
                    logger.warning(
                        "regen_for_user: no evergreen patch for module=%s lang=%s target=%s",
                        module, language, target,
                    )

        # ── 8: Guard — don't persist if everything is empty ─────────────────
        if not any(q.get("text") for q in final):
            logger.error(
                "regen_for_user: all 10 slots empty after patching — aborting "
                "(user=%s pet_id=%s lang=%s)", user_code, pet_id, language,
            )
            return

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()

        # ── 9a: Write to Postgres first (source of truth) ───────────────────
        await suggested_repo.upsert(
            user_code=user_code,
            language=language,
            pet_id=pet_id,
            questions=final,
            generated_at=now_utc,
        )

        # ── 9b: Write to Valkey (hot cache) — after DB succeeds ─────────────
        cache_key = CacheKeys.suggested_questions(user_code, language, pet_id)
        await valkey.setex(
            cache_key,
            jittered_ttl(TTL_SUGGESTED),
            json.dumps({"generated_at": now_iso, "questions": final}),
        )

        # ── 10: Update 4-week history (shared across all pets) ─────────────────
        # All 10 generated questions are stored, not only the 3 served by /setup.
        # This ensures next-cycle regen never reproduces the same pool — the LLM
        # is told "avoid these" regardless of whether each question was displayed.
        current_week = now_utc.strftime("%G-W%V")
        cutoff_week = (now_utc - timedelta(weeks=4)).strftime("%G-W%V")
        history_entries = [
            e for e in history_entries if e.get("week", "") >= cutoff_week
        ]
        history_entries.append({
            "week": current_week,
            "questions": [q["text"] for q in final if q.get("text")],
        })
        await valkey.setex(
            history_key,
            jittered_ttl(TTL_SUGGESTED_HISTORY),
            json.dumps(history_entries),
        )

        logger.info(
            "regen_for_user: complete (user=%s pet_id=%s lang=%s)",
            user_code, pet_id, language,
        )

    except Exception as exc:
        logger.error(
            "regen_for_user: failed (user=%s pet_id=%s lang=%s error=%s)",
            user_code, pet_id, language, exc,
        )


# ── Evergreen fill helper ────────────────────────────────────────────────────

def _build_full_evergreen(language: str, is_pet_b: bool = False) -> list[dict]:
    """
    Build a full 10-question evergreen set for one pet.

    Slot layout (per-pet design):
      slots 0-2 → food   / this_pet   (3 dedicated)
      slot  3   → food   / both
      slots 4-6 → health / this_pet   (3 dedicated)
      slot  7   → health / both
      slot  8   → anymall / this_pet
      slot  9   → anymall / both
    """
    tgt = "pet_b" if is_pet_b else "pet_a"
    slots: list[dict] = []

    # food: 3 dedicated + 1 both
    for _ in range(3):
        qs = get_evergreen_questions("food", language, tgt, count=1)
        slots.append(qs[0] if qs else {"text": "", "module": "food", "target": tgt})
    qs = get_evergreen_questions("food", language, "both", count=1)
    slots.append(qs[0] if qs else {"text": "", "module": "food", "target": "both"})

    # health: 3 dedicated + 1 both
    for _ in range(3):
        qs = get_evergreen_questions("health", language, tgt, count=1)
        slots.append(qs[0] if qs else {"text": "", "module": "health", "target": tgt})
    qs = get_evergreen_questions("health", language, "both", count=1)
    slots.append(qs[0] if qs else {"text": "", "module": "health", "target": "both"})

    # anymall: 1 dedicated + 1 both
    qs = get_evergreen_questions("anymall", language, tgt, count=1)
    slots.append(qs[0] if qs else {"text": "", "module": "anymall", "target": tgt})
    qs = get_evergreen_questions("anymall", language, "both", count=1)
    slots.append(qs[0] if qs else {"text": "", "module": "anymall", "target": "both"})

    return slots
