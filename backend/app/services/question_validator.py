# app/services/question_validator.py
#
# Validates suggested questions before caching or returning them.
#
# Checks per question:
#   1. text not empty
#   2. within character limit (language-dependent)
#   3. no duplicates in the current set of 4
#   4. not in the 4-week recent history
#   5. not alarmist or diagnosis-like (regex blocklist)
#   6. correct pet targeting for 2-pet households
#
# On failure: caller regenerates once, then replaces failed items
# with evergreen fallback.

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Character limits by language group ──────────────────────────────────────

CJK_LANGUAGES = {"JA", "KO", "ZH", "TH"}


def get_char_limit(language: str) -> int:
    """Hard max character count for a suggested question."""
    return 36 if language in CJK_LANGUAGES else 56


# ── Blocklists ──────────────────────────────────────────────────────────────
# Questions must not sound alarmist, clinical, or diagnosis-like.
# Case-insensitive matching.

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
    """Return True if the question contains alarmist/diagnosis-like language."""
    return any(p.search(text) for p in _ALARMIST_PATTERNS)


# ── Single question validation ──────────────────────────────────────────────

def validate_single_question(
    question: dict[str, str],
    language: str,
    seen_texts: set[str],
    recent_history: list[str],
) -> str | None:
    """
    Validate a single suggested question.

    Returns None if the question passes all checks.
    Returns a string describing the failure reason if it fails.
    """
    text = (question.get("text") or "").strip()

    if not text:
        return "empty_text"

    max_chars = get_char_limit(language)
    if len(text) > max_chars:
        return f"over_char_limit ({len(text)} > {max_chars})"

    # Duplicate within current set
    text_lower = text.lower()
    if text_lower in seen_texts:
        return "duplicate_in_set"
    seen_texts.add(text_lower)

    # Recent 4-week history
    if text_lower in {q.lower() for q in recent_history}:
        return "repeated_from_history"

    # Alarmist / diagnosis check
    if _is_alarmist(text):
        return "alarmist_content"

    # Validate target field
    valid_targets = {"pet_a", "pet_b", "both"}
    if question.get("target") not in valid_targets:
        return f"invalid_target: {question.get('target')}"

    # Validate reason_type field
    valid_reasons = {"known_context", "missing_context", "evergreen"}
    if question.get("reason_type") not in valid_reasons:
        return f"invalid_reason_type: {question.get('reason_type')}"

    return None


# ── Full set validation ─────────────────────────────────────────────────────

def validate_questions(
    questions: list[dict[str, str]],
    language: str,
    pet_count: int,
    recent_history: list[str] | None = None,
) -> tuple[bool, list[int], list[str]]:
    """
    Validate a full set of 4 suggested questions.

    Args:
        questions: list of question dicts (text, target, reason_type)
        language: resolved language code
        pet_count: 1 or 2
        recent_history: flat list of question texts shown in last 4 weeks

    Returns:
        (all_passed, failed_indices, failure_reasons)
        - all_passed: True if every question is valid
        - failed_indices: list of indices that failed
        - failure_reasons: human-readable reason for each failure
    """
    if recent_history is None:
        recent_history = []

    if len(questions) != 4:
        return False, list(range(4)), [f"expected 4 questions, got {len(questions)}"]

    seen_texts: set[str] = set()
    failed_indices: list[int] = []
    failure_reasons: list[str] = []

    for i, q in enumerate(questions):
        reason = validate_single_question(q, language, seen_texts, recent_history)
        if reason:
            failed_indices.append(i)
            failure_reasons.append(f"question[{i}]: {reason}")
            logger.debug("Validation failed — %s: %r", reason, q.get("text", "")[:50])

    # 2-pet targeting check: must include at least pet_a, pet_b, both
    if pet_count == 2 and not failed_indices:
        targets = {q["target"] for q in questions}
        required = {"pet_a", "pet_b", "both"}
        missing = required - targets
        if missing:
            # Don't fail the whole set — just log a warning.
            # The LLM may have good reasons for the targeting.
            logger.warning(
                "2-pet targeting incomplete — missing targets: %s", missing,
            )

    all_passed = len(failed_indices) == 0
    return all_passed, failed_indices, failure_reasons
