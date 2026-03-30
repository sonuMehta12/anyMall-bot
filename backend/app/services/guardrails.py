# app/services/guardrails.py
#
# Two functions:
#
#   detect_prompt_injection(message) → called BEFORE any LLM call.
#       Pattern-matches the user's raw input against known injection phrases.
#       Returns True if the message looks like a jailbreak/injection attempt.
#       If True, the route returns 400 immediately — no LLM call, no background task.
#       This is Layer 1a of the input security model (T1-04).
#
#   apply_guardrails(response) → called AFTER Agent 1 generates a reply.
#       Reads the reply with regex.
#       Removes/rewrites blocked phrases.
#       Returns the safe, cleaned reply string.
#
# Intent classification (health/food/general/untrusted + urgency) is handled by
# IntentClassifier in app/agents/intent_classifier.py — LLM-based, not regex.
# That is Layer 1b: catches subtle attacks that patterns miss.
#
# Both functions have no side effects and no global state. Easy to unit test.

import logging
import re
from dataclasses import dataclass, field

from constants import (
    BLOCKED_MEDICAL_JARGON,
    PREACHY_PHRASES,
)

logger = logging.getLogger(__name__)


# ── Pre-compiled injection detection patterns (Layer 1a) ──────────────────────
#
# Compiled ONCE at module load. Reused on every request. Zero cost per call.
#
# Design choices:
#   - Patterns are specific enough to avoid false positives in a pet chat context.
#     ("ignore" alone is too broad; "ignore.*instructions" is precise.)
#   - All patterns are case-insensitive (re.IGNORECASE).
#   - 10 patterns cover the vast majority of known injection attack families.
#   - The IntentClassifier (LLM) catches subtle attacks that patterns miss.

_INJECTION_PATTERNS: list[re.Pattern] = [
    # "ignore all/previous/your instructions"
    re.compile(r"ignore\s+(all\s+|previous\s+|your\s+)?instructions?", re.IGNORECASE),
    # "forget everything above / forget your instructions / forget all rules"
    re.compile(
        r"forget\s+(everything|all|your|previous)\s+(above|instructions?|context|rules?)",
        re.IGNORECASE,
    ),
    # "disregard / override / bypass [all] [your] instructions / rules / guidelines"
    re.compile(
        r"(disregard|override|bypass)\s+(all\s+)?(your\s+)?(previous\s+)?"
        r"(instructions?|rules?|guidelines?|constraints?)",
        re.IGNORECASE,
    ),
    # "reveal / show / print / repeat [your] system prompt"
    re.compile(
        r"(reveal|show|print|display|repeat)\s+(me\s+)?(your\s+)?system\s+prompt",
        re.IGNORECASE,
    ),
    # "you are now DAN / unrestricted / jailbroken / evil"
    re.compile(
        r"you\s+are\s+now\s+(dan|unrestricted|evil|jailbroken|freed|unfiltered)",
        re.IGNORECASE,
    ),
    # "act as if you have no restrictions / act as an unrestricted AI"
    re.compile(
        r"act\s+as\s+(if\s+you\s+have\s+no\s+|an?\s+unrestricted\s+)",
        re.IGNORECASE,
    ),
    # "pretend to be / pretend you are [an unrestricted / evil / hacked AI]"
    re.compile(
        r"pretend\s+(to\s+be|you\s+are)\s+(an?\s+)?(unrestricted|evil|hacked|jailbroken)",
        re.IGNORECASE,
    ),
    # "jailbreak" and all variants: jailbreaking, jailbreaked, jailbreaks
    re.compile(r"\bjailbreak", re.IGNORECASE),
    # "new instructions:" — a classic injection prefix
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    # "your new role / persona / instructions / prompt is..."
    re.compile(
        r"your\s+new\s+(role|persona|instructions?|prompt|task)",
        re.IGNORECASE,
    ),
]


# ── Pre-compiled guardrail patterns (LLM output) ──────────────────────────────
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


# ── detect_prompt_injection ────────────────────────────────────────────────────

def detect_prompt_injection(message: str) -> bool:
    """
    Return True if the message looks like a prompt injection or jailbreak attempt.

    Checks the raw user input against _INJECTION_PATTERNS (compiled at module
    load). This runs BEFORE any LLM call — a True result means the route
    returns 400 immediately, and no background pipeline is created.

    This is Layer 1a of the input security model (T1-04). Layer 1b is the
    IntentClassifier returning "untrusted" for subtler attacks.

    Args:
        message: Raw user message string (already stripped of whitespace).

    Returns:
        True if any injection pattern matches. False if the message is clean.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(message):
            logger.warning(
                "Prompt injection detected. pattern=%r snippet=%r",
                pattern.pattern, message[:80],
            )
            return True
    return False
