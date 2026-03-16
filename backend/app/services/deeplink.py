# app/services/deeplink.py
#
# Builds the redirect payload when a health or food intent is detected.
#
# What lives here:
#   - DeeplinkPayload  — a typed dataclass describing the redirect data
#   - build_deeplink() — pure function: intent + message → payload or None
#
# Design principle: Backend says WHAT (module, context), client decides HOW to navigate.
# No URLs in the payload — Flutter uses screen routes, React opens simulator pages.
#
# pet_summary is included BY DESIGN — the health/food module needs full pet context
# to give good advice. This is intentional, not a hack.

import logging
from dataclasses import dataclass

from constants import (
    INTENT_HEALTH,
    INTENT_FOOD,
    URGENCY_HIGH,
)

logger = logging.getLogger(__name__)


# ── DeeplinkPayload ────────────────────────────────────────────────────────────
#
# Every field the client needs to:
#   1. Navigate to the correct module (module)
#   2. Show the redirect button (display_label, display_style)
#   3. Pass context to the target module (query, pet_id, pet_summary)

@dataclass
class DeeplinkPayload:
    module: str           # "health" | "food" — which module to navigate to
    urgency: str          # "high" | "medium" — from IntentClassifier
    display_label: str    # button text: "Talk to Health Assistant" etc.
    display_style: str    # "urgent" (red) | "suggestion" (orange)
    query: str            # user's original message, pre-filled in the module
    pet_id: int           # which pet, so the module can fetch its own data
    pet_summary: str      # full NL pet context — by design, module needs this


# ── build_deeplink ─────────────────────────────────────────────────────────────

def build_deeplink(
    intent_type: str,
    urgency: str,
    user_message: str,
    pet_summary: str,
    pet_id: int,
) -> DeeplinkPayload | None:
    """
    Build a redirect payload if the intent requires one. Returns None for general messages.

    Args:
        intent_type:   Output of IntentClassifier — "health", "food", or "general".
        urgency:       Output of IntentClassifier — "high", "medium", or "low".
        user_message:  The raw user message, pre-filled in the target module.
        pet_summary:   NL string describing the pet (from context_builder.py).
        pet_id:        Which pet this conversation is about.

    Returns:
        DeeplinkPayload if intent is health or food.
        None if intent is general — no redirect needed.
    """
    style = "urgent" if urgency == URGENCY_HIGH else "suggestion"

    if intent_type == INTENT_HEALTH:
        payload = DeeplinkPayload(
            module="health",
            urgency=urgency,
            display_label="Talk to Health Assistant",
            display_style=style,
            query=user_message,
            pet_id=pet_id,
            pet_summary=pet_summary,
        )
        logger.info("build_deeplink → health redirect built, urgency=%s", urgency)
        return payload

    if intent_type == INTENT_FOOD:
        payload = DeeplinkPayload(
            module="food",
            urgency=urgency,
            display_label="Talk to Food Specialist",
            display_style=style,
            query=user_message,
            pet_id=pet_id,
            pet_summary=pet_summary,
        )
        logger.info("build_deeplink → food redirect built, urgency=%s", urgency)
        return payload

    # General message — no redirect
    logger.debug("build_deeplink → no redirect (intent_type=%s)", intent_type)
    return None
