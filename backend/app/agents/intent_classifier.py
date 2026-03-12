# app/agents/intent_classifier.py
#
# LLM-based intent classifier — runs BEFORE Agent 1 on every request.
#
# Why LLM instead of regex?
#   Regex cannot handle negation ("Luna is NOT vomiting"), past tense
#   ("she had a seizure last year but is fine now"), or context
#   ("vet said everything looks great"). LLM understands all of these.
#
# Why a separate agent and not part of Agent 1?
#   Agent 1 needs intent BEFORE it generates a reply — the intent is injected
#   into Agent 1's system prompt. If Agent 1 classified intent in the same call,
#   we'd have a chicken-and-egg problem.
#
# Retry policy (key design decision):
#   Bad LLM output (bad JSON, unknown values, low confidence) → retry.
#   The LLM made a mistake — ask again.
#
#   LLMProviderError (API down, network error) → fallback to general immediately.
#   The service is unavailable — retrying won't help.
#
#   Max 2 attempts total. After exhausting retries:
#     - Still bad output → fallback to ("general", "low"). Safest default.
#     - Low confidence but valid values → use the result. Best we can do.

import json
import logging

from app.llm.base import LLMProvider, LLMProviderError
from constants import (
    INTENT_GENERAL,
    INTENT_HEALTH,
    INTENT_FOOD,
    URGENCY_HIGH,
    URGENCY_MEDIUM,
    URGENCY_LOW,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

MAX_ATTEMPTS: int = 2         # 1 original + 1 retry
CONFIDENCE_THRESHOLD: int = 5  # retry if confidence < 5 (scale 1–10)

_VALID_INTENTS: frozenset[str] = frozenset({INTENT_HEALTH, INTENT_FOOD, INTENT_GENERAL})
_VALID_URGENCIES: frozenset[str] = frozenset({URGENCY_HIGH, URGENCY_MEDIUM, URGENCY_LOW})


# ── Classifier prompt ──────────────────────────────────────────────────────────
#
# Constraints baked into the prompt:
#   - Negation: "NOT vomiting", "stopped limping" → general
#   - Past/resolved: "had a seizure last year but is fine" → general
#   - Casual vet mention: "vet said she's great" → general
#   - Current concern: "limping since yesterday" → health/medium
#   - Explicit food advice request → food
#
# temperature=0.0 — classification is deterministic, not creative.
# max_tokens=48   — just a small JSON object, nothing more.

_CLASSIFIER_SYSTEM_PROMPT = """\
You classify pet owner messages for a pet companion app. \
Reply with ONLY a JSON object — no explanation, no markdown, no extra keys.

Classification rules:
- "health": owner describes a CURRENT symptom, active concern, injury, or asks \
a medical/vet question about something happening NOW
- "food": owner asks for diet advice, feeding recommendations, or nutrition guidance
- "general": everything else — greetings, happy updates, behaviour questions, \
past/resolved issues, or vet visits that went well

Urgency rules (only applies when intent is "health"):
- "high"  : emergency signals RIGHT NOW — vomiting, seizure, bleeding, collapse, \
not breathing, poisoning, unconscious, pale gums
- "medium": concerning but not emergency — limping, lethargy, not eating, swelling, \
diarrhoea, unusual behaviour
- "low"   : routine health question or check-in with no acute symptom
For "general", always set urgency to "low".
For "food", use the same urgency scale: \
"high" = toxic food emergency (e.g., chocolate, xylitol ingestion), \
"medium" = feeding concern (e.g., refusal to eat, sudden diet change problems), \
"low" = routine diet question (e.g., "what food is best?").

Critical edge cases:
- "Luna is NOT vomiting anymore" → general (negated / resolved)
- "she had a seizure last year but is fine now" → general (past, resolved)
- "vet said everything looks great" → general (vet visit, positive outcome)
- "Luna has been vomiting since morning" → health, high (active, current)
- "Luna seems a bit tired today" → health, medium (current, concerning)
- "what should Luna eat?" → food

Confidence: rate your certainty 1–10. If the message is ambiguous, score lower.

Required JSON format (strict, no deviation):
{"intent": "health"|"food"|"general", "urgency": "high"|"medium"|"low", "confidence": 1-10}"""


# ── IntentClassifier ───────────────────────────────────────────────────────────

class IntentClassifier:
    """
    Classifies a user message into (intent_type, urgency) using the LLM.

    Follows the same constructor pattern as ConversationAgent:
    receives an LLMProvider, never imports a concrete provider directly.
    Created once at startup and shared across all requests.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        logger.info("IntentClassifier initialised.")

    async def classify(self, message: str) -> tuple[str, str]:
        """
        Classify a user message and return (intent_type, urgency).

        Retry policy:
          - Bad JSON, unknown values, or confidence < CONFIDENCE_THRESHOLD → retry.
          - LLMProviderError → fallback to ("general", "low") immediately, no retry.
          - After MAX_ATTEMPTS with still-invalid output → fallback to ("general", "low").
          - After MAX_ATTEMPTS with low-confidence but valid output → use the result.

        Args:
            message: The raw user message text.

        Returns:
            (intent_type, urgency) as string constants from constants.py.
        """
        last_valid_result: tuple[str, str] | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = await self._llm.complete(
                    system_prompt=_CLASSIFIER_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": message}],
                    temperature=0.0,   # deterministic — classification, not generation
                    max_tokens=48,     # {"intent":"health","urgency":"high","confidence":9} + slack
                )
            except LLMProviderError as exc:
                # Infrastructure problem — retrying won't help.
                logger.error(
                    "IntentClassifier attempt %d: LLMProviderError (%s) — fallback to general",
                    attempt, exc,
                )
                return INTENT_GENERAL, URGENCY_LOW

            # ── Parse ──────────────────────────────────────────────────────────
            parsed = _parse_response(raw)

            if parsed is None:
                logger.warning(
                    "IntentClassifier attempt %d: unparseable response %r%s",
                    attempt, raw[:120],
                    " — retrying" if attempt < MAX_ATTEMPTS else " — fallback to general",
                )
                continue  # retry if attempts remain, else loop ends → fallback below

            intent, urgency, confidence = parsed

            # ── Validate ───────────────────────────────────────────────────────
            invalid_intent = intent not in _VALID_INTENTS
            invalid_urgency = urgency not in _VALID_URGENCIES

            if invalid_intent or invalid_urgency:
                logger.warning(
                    "IntentClassifier attempt %d: invalid values intent=%r urgency=%r%s",
                    attempt, intent, urgency,
                    " — retrying" if attempt < MAX_ATTEMPTS else " — fallback to general",
                )
                continue  # retry

            # We have valid values — store in case next check triggers a retry.
            last_valid_result = (intent, urgency)

            # ── Confidence check ───────────────────────────────────────────────
            if confidence < CONFIDENCE_THRESHOLD:
                logger.warning(
                    "IntentClassifier attempt %d: low confidence %d (threshold %d) "
                    "intent=%s urgency=%s%s",
                    attempt, confidence, CONFIDENCE_THRESHOLD, intent, urgency,
                    " — retrying" if attempt < MAX_ATTEMPTS else " — using low-confidence result",
                )
                if attempt < MAX_ATTEMPTS:
                    continue  # retry

                # Exhausted retries with low confidence — use the result anyway.
                logger.info(
                    "IntentClassifier: using low-confidence result after %d attempts "
                    "intent=%s urgency=%s confidence=%d",
                    MAX_ATTEMPTS, intent, urgency, confidence,
                )
                return intent, urgency

            # ── Success ────────────────────────────────────────────────────────
            logger.info(
                "IntentClassifier: intent=%s urgency=%s confidence=%d (attempt %d)",
                intent, urgency, confidence, attempt,
            )
            return intent, urgency

        # Loop exhausted — only reaches here if every attempt had unparseable/invalid output.
        if last_valid_result is not None:
            logger.warning(
                "IntentClassifier: using last valid result after failed attempts: %s",
                last_valid_result,
            )
            return last_valid_result

        logger.error(
            "IntentClassifier: all %d attempts failed with invalid output — fallback to general",
            MAX_ATTEMPTS,
        )
        return INTENT_GENERAL, URGENCY_LOW


# ── _parse_response ────────────────────────────────────────────────────────────

def _parse_response(raw: str) -> tuple[str, str, int] | None:
    """
    Parse the LLM's JSON response into (intent, urgency, confidence).

    Returns None if the response cannot be parsed or is missing required keys.
    Does NOT validate that the values are in the allowed sets — that is the
    caller's job so we can log meaningful errors.
    """
    try:
        # Strip markdown code fences if the LLM added them despite instructions.
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)

        intent = str(data["intent"])
        urgency = str(data["urgency"])
        confidence = int(data["confidence"])

        return intent, urgency, confidence

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.debug("_parse_response failed: %s | raw=%r", exc, raw[:120])
        return None
