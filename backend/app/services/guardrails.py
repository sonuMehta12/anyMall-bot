# app/services/guardrails.py
#
# One pure function that runs AFTER Agent 1:
#
#   apply_guardrails(response) → called after Agent 1 generates a reply.
#       Reads the reply with regex.
#       Removes/rewrites blocked phrases.
#       Returns the safe, cleaned reply string.
#
# Intent classification (health/food/general + urgency) is handled by
# IntentClassifier in app/agents/intent_classifier.py — LLM-based, not regex.
#
# This function has no side effects and no global state.
# Takes a string in, returns a result. Easy to unit test.

import logging
import re
from dataclasses import dataclass, field

from constants import (
    BLOCKED_MEDICAL_JARGON,
    PREACHY_PHRASES,
)

logger = logging.getLogger(__name__)


# ── Pre-compiled guardrail patterns ───────────────────────────────────────────
#
# Compiled ONCE at module load. Reused on every request.
#
# Why pre-compile?
#   re.compile() parses the pattern string and builds an internal regex object.
#   If we called re.compile() inside apply_guardrails(), it would rebuild these
#   objects on every single chat request — wasteful and slow at scale.

_BLOCKED_JARGON_PATTERNS: list[tuple[str, re.Pattern]] = [
    (phrase, re.compile(re.escape(phrase), re.IGNORECASE))
    for phrase in BLOCKED_MEDICAL_JARGON
]

_PREACHY_PATTERNS: list[tuple[str, re.Pattern]] = [
    (phrase, re.compile(re.escape(phrase), re.IGNORECASE))
    for phrase in PREACHY_PHRASES
]


# ── GuardrailResult ────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    """
    The result of apply_guardrails().
    Contains the cleaned reply and a log of what was changed.
    """
    reply: str
    was_modified: bool = False
    modifications: list[str] = field(default_factory=list)


# ── apply_guardrails ───────────────────────────────────────────────────────────

def apply_guardrails(response: str) -> GuardrailResult:
    """
    Clean an Agent 1 reply before it goes to the user.

    Uses pre-compiled patterns (_BLOCKED_JARGON_PATTERNS, _PREACHY_PATTERNS)
    — compiled once at startup, never re-compiled per request.

    Checks for:
      1. BLOCKED_MEDICAL_JARGON — phrases that sound like a vet diagnosis
      2. PREACHY_PHRASES        — moralising language

    Args:
        response: Agent 1's raw reply string.

    Returns:
        GuardrailResult with the safe reply and a log of changes.
    """
    result = GuardrailResult(reply=response)
    cleaned = response

    # ── 1. Remove blocked medical jargon ──────────────────────────────────────
    for phrase, pattern in _BLOCKED_JARGON_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[consult your vet about this]", cleaned)
            result.was_modified = True
            result.modifications.append(f"blocked_jargon: {phrase!r}")
            logger.info("Guardrail: removed blocked jargon %r", phrase)

    # ── 2. Soften preachy phrases ─────────────────────────────────────────────
    for phrase, pattern in _PREACHY_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            result.was_modified = True
            result.modifications.append(f"preachy: {phrase!r}")
            logger.info("Guardrail: removed preachy phrase %r", phrase)

    # ── 3. Clean up double spaces left by removals ─────────────────────────────
    if result.was_modified:
        cleaned = re.sub(r"  +", " ", cleaned).strip()

    result.reply = cleaned

    if result.was_modified:
        logger.info("apply_guardrails: modified. changes=%s", result.modifications)
    else:
        logger.debug("apply_guardrails: reply passed clean.")

    return result
