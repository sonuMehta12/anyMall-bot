# app/services/question_generation/validator.py
#
# Per-slot validation for v2 suggested questions (10-question per-pet cache).
#
# Public API:
#   validate_slot(question, slot_index, all_questions, language, pet_count, history)
#       -> str | None   (None = valid; string = failure reason)
#   get_char_limit(language) -> int
#
# Validation rules (applied per slot):
#   1. text not empty
#   2. len(text) <= char limit (CJK: 36, others: 56)
#   3. not a duplicate of any other slot in the 10 (case-insensitive)
#   4. not in 4-week history
#   5. not alarmist / diagnosis-like (14 patterns)
#   6. module in {"food", "health", "anymall"}
#   7. target in {"pet_a", "pet_b", "both"}
#   8. single-pet guard: if pet_count==1, reject multi-pet language in the text
#      (checked against text regardless of target — a mislabelled target must not bypass this)

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── Character limits by language group ──────────────────────────────────────

CJK_LANGUAGES = {"JA", "KO", "ZH", "TH"}


def get_char_limit(language: str) -> int:
    """Hard max character count for a suggested question."""
    lang = language.upper() if language else "EN"
    return 36 if lang in CJK_LANGUAGES else 56


# ── Alarmist / clinical blocklist ───────────────────────────────────────────
# 14 patterns copied exactly from v1. Case-insensitive.

_ALARMIST_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bemergency\b",
        r"\brush to vet\b",
        r"\bdying\b",
        r"\bimmediately\b",
        r"\bdiagnos",             # diagnose, diagnosis, diagnosed
        r"\bprescription\b",
        r"\btreatment plan\b",
        r"\bmedication dosing\b",
        r"\bsurgery\b",
        r"\beuthan",              # euthanasia, euthanize
        r"\bparalyz",             # paralyzed, paralysis
        r"\bseizure\b",
        r"\bbleeding\b",
        r"\bpoisoning\b",
    ]
]


def _is_alarmist(text: str) -> bool:
    return any(p.search(text) for p in _ALARMIST_PATTERNS)


# ── Single-pet guard ─────────────────────────────────────────────────────────
# If there is only one pet, reject language that implies multiple pets.

_MULTI_PET_PHRASES = [
    "both pets",
    "two pets",
    "both my",
    "other pet",   # "my other pet", "other pet's" — used in all pet_b templates
    "second pet",  # "my second pet", "second pet's" — used in all pet_b templates
    "両方",        # "both" (JA)
    "二匹",        # "two animals" (JA)
    "2匹目",       # "second pet" (JA) — used in all JA pet_b templates
    "もう1匹",     # "another pet" (JA) — used in JA pet_b templates
]


def _has_multi_pet_language(text: str) -> bool:
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in _MULTI_PET_PHRASES)


# ── Per-slot validation ──────────────────────────────────────────────────────

_VALID_MODULES = {"food", "health", "anymall"}
_VALID_TARGETS = {"pet_a", "pet_b", "both"}


def validate_slot(
    question: dict,
    slot_index: int,
    all_questions: list[dict],
    language: str,
    pet_count: int,
    history: list[str],
) -> str | None:
    """
    Validate a single question slot from the 10-question set.

    Args:
        question:      Dict with keys "text", "module", "target"
        slot_index:    Index of this slot within all_questions (0–9)
        all_questions: Full list of 10 question dicts (for duplicate check)
        language:      Resolved language code (EN, JA, KO, …)
        pet_count:     1 or 2
        history:       Flat list of question texts shown in last 4 weeks

    Returns:
        None if valid, or a short string describing the failure.
    """
    lang = (language or "EN").upper()
    text = (question.get("text") or "").strip()

    # 1. Non-empty text
    if not text:
        return "empty_text"

    # 2. Character limit
    max_chars = get_char_limit(lang)
    if len(text) > max_chars:
        return f"over_char_limit ({len(text)} > {max_chars})"

    # 3. Duplicate within the 10-question set (skip comparing slot to itself)
    text_lower = text.lower()
    for i, other in enumerate(all_questions):
        if i == slot_index:
            continue
        other_text = (other.get("text") or "").strip().lower()
        if other_text and other_text == text_lower:
            return f"duplicate_of_slot_{i}"

    # 4. In 4-week history
    history_lower = {q.lower() for q in history}
    if text_lower in history_lower:
        return "repeated_from_history"

    # 5. Alarmist / clinical language
    if _is_alarmist(text):
        return "alarmist_content"

    # 6. module field
    module = question.get("module")
    if module not in _VALID_MODULES:
        return f"invalid_module: {module!r}"

    # 7. target field
    target = question.get("target")
    if target not in _VALID_TARGETS:
        return f"invalid_target: {target!r}"

    # 8. Single-pet guard: reject multi-pet language in text regardless of target.
    # Checking target first (as before) would miss questions mislabelled as pet_a
    # whose text still says "other pet" / "second pet" / "both my".
    if pet_count == 1 and _has_multi_pet_language(text):
        return "single_pet_multi_pet_language"

    return None
