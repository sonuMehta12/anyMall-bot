# app/services/deeplink.py
#
# Builds the redirect payload when a health or food intent is detected.
#
# What lives here:
#   - DeeplinkPayload  — a typed dataclass describing every field the mobile
#                        app needs to navigate to the Health or Food module
#   - build_deeplink() — pure function: IntentFlags + message → payload or None
#
# What does NOT live here:
#   - Intent detection  (guardrails.py)
#   - Response cleaning (guardrails.py)
#   - HTTP routing      (main.py)
#
# Why a separate file?
#   guardrails.py is about reading and cleaning text.
#   deeplink.py is about building a navigation payload.
#   Keeping them separate means each file has one clear job.
#
# Phase 1 note:
#   pet_summary carries the full NL pet profile inline in the payload.
#   In Phase 2 (Redis), this becomes a pet_context_key that the Health/Food
#   module fetches itself — same information, different delivery mechanism.
#   Changing that requires only a small edit to build_deeplink() here.

import logging
from dataclasses import dataclass

from constants import (
    INTENT_HEALTH,
    INTENT_FOOD,
    URGENCY_LOW,
)

logger = logging.getLogger(__name__)


# ── DeeplinkPayload ────────────────────────────────────────────────────────────
#
# Every field the mobile app needs to:
#   1. Navigate to the correct module (module + deep_link)
#   2. Pre-fill the user's question (pre_populated_query)
#   3. Give the module Luna's context (pet_summary)
#   4. Style the button correctly (urgency)

@dataclass
class DeeplinkPayload:
    module: str               # "health" | "food" — which module to open
    deep_link: str            # full URL to navigate to (localhost in Phase 1)
    pre_populated_query: str  # user's original message, pre-filled in the module's input
    pet_summary: str          # NL pet profile — so health/food module knows about this pet
    urgency: str              # "high" | "medium" | "low" — drives button colour + alert


# ── build_deeplink ─────────────────────────────────────────────────────────────

def build_deeplink(
    intent_type: str,
    urgency: str,
    user_message: str,
    pet_summary: str,
    base_url: str = "http://localhost:8000",
) -> DeeplinkPayload | None:
    """
    Build a redirect payload if the intent requires one. Returns None for general messages.

    Args:
        intent_type:   Output of IntentClassifier — "health", "food", or "general".
        urgency:       Output of IntentClassifier — "high", "medium", or "low".
        user_message:  The raw user message. Goes into pre_populated_query so the
                       user doesn't have to retype it in the Health/Food module.
        pet_summary:   NL string describing the pet (from context_builder.py).
                       Passed inline so the module has context without a Redis lookup.
        base_url:      Root URL for the simulator links. Defaults to localhost:8000.
                       In production: change to the real mobile deeplink scheme.

    Returns:
        DeeplinkPayload if intent is health or food.
        None if intent is general — no redirect needed.
    """
    if intent_type == INTENT_HEALTH:
        payload = DeeplinkPayload(
            module="health",
            deep_link=f"{base_url}/health/chat",
            pre_populated_query=user_message,
            pet_summary=pet_summary,
            urgency=urgency,
        )
        logger.info("build_deeplink → health redirect built, urgency=%s", urgency)
        return payload

    if intent_type == INTENT_FOOD:
        payload = DeeplinkPayload(
            module="food",
            deep_link=f"{base_url}/food/chat",
            pre_populated_query=user_message,
            pet_summary=pet_summary,
            urgency=URGENCY_LOW,   # food questions are never high-urgency
        )
        logger.info("build_deeplink → food redirect built")
        return payload

    # General message — no redirect
    logger.debug("build_deeplink → no redirect (intent_type=%s)", intent_type)
    return None
