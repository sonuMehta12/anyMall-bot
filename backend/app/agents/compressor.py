# app/agents/compressor.py
#
# Agent 2 — the Compressor.
#
# Runs fire-and-forget AFTER the chat reply is sent to the user.
# Reads the user message from AgentState and extracts structured facts about the pet.
# Never talks to the user. Never blocks the chat endpoint.
#
# How this file is organised:
#   1. COMPRESSOR_SYSTEM_PROMPT — the full extraction prompt (one readable place)
#   2. ExtractedFact            — dataclass matching design-docs/compressor-design.md schema
#   3. _parse_compressor_response — strips fences, parses JSON, validates fields
#   4. CompressorAgent          — the agent class with run()
#
# Design decisions (see design-docs/compressor-design.md for full rationale):
#   - Only runs when state.is_entity is True (set by ConversationAgent)
#   - Returns ALL facts with confidence >= 0.50 — caller (main.py) splits by threshold
#   - On any failure: logs error and returns [] — never raises, never crashes background task
#   - temperature=0.0 — extraction is deterministic, not creative
#   - max_tokens=600  — enough for ~5 facts in JSON (increased for dual-pet output)

import json
import logging
import re
from dataclasses import dataclass

from app.agents.state import AgentState
from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)


# ── Compressor system prompt ────────────────────────────────────────────────────
#
# This is the FULL system prompt the Compressor sends on every extraction call.
# Read this constant and you know exactly what the LLM sees — no hunting.
#
# Notes on format:
#   - No {placeholders} in this string — it is used as-is as the system message.
#   - The user message (with pet context + history) is built separately in
#     _build_user_prompt() and sent as the "user" role message.

COMPRESSOR_SYSTEM_PROMPT = """\
You are a structured fact extractor for a pet health app.
Your only job: extract factual claims about the pet from the user message.
Return ONLY valid JSON. No explanation. No markdown.

EXTRACTION RULES:
1. Extract ONLY facts explicitly stated. Never infer or guess.
2. time_scope: "current" if present tense, "past" if past tense, "unknown" if unclear.
3. source_rank:
   - "vet_record" if user mentions vet / doctor / test result / lab work.
   - "user_correction" if user is explicitly correcting a previous statement \
(phrases like "actually", "no it's", "I was wrong", "not X it's Y", "I meant", \
"correction"). Confidence stays based on the corrected fact's certainty.
   - Else "explicit_owner".
4. Confidence scoring:
   - 0.95 — hard specific fact ("vet confirmed exactly 4.2kg")
   - 0.85 — stated confidently, no hedging ("Luna weighs 4kg")
   - 0.75 — mild hedging ("about 4kg", "roughly", "around")
   - 0.60 — clear uncertainty ("I think", "maybe", "probably")
   - 0.50 — speculative or second-hand ("I heard", "someone told me")
5. uncertainty: plain text reason why confidence < 1.0. Empty string if fully confident.
6. Negative facts are valid: "no allergies" → key="allergies", value="none confirmed".
7. Normalize units: "4 kilos" → value="4 kg", key="weight". Always use standard units.
8. timestamp: ISO string only when user explicitly states a specific date or time. \
Otherwise null.
9. Extract ALL facts in one call. Multiple facts = multiple entries in the array.
10. If nothing is extractable: return {"facts": []}.
11. PET LABELING (dual-pet sessions only):
   If TWO pets are provided in the context (Pet A and Pet B), label each fact \
with "pet_label": "pet_a" or "pet_b".
   Determine which pet by: explicit name mention, pronoun context from recent \
conversation, or species context.
   If truly ambiguous (cannot determine which pet), default to "pet_a" and set \
uncertainty="ambiguous pet attribution — could not determine which pet".
   If only ONE pet is provided, always use "pet_a".

PREFERRED KEY NAMES (use these when applicable — snake_case):
name, breed, age, weight, sex, neutered_spayed, diet_type, food_brand, allergies,
chronic_illness, past_conditions, medications, past_medications,
vaccinations, vet_name, last_vet_visit, energy_level, temperament,
behavioral_traits, appetite, activity_level, microchipped, insurance
For anything not in this list: use a descriptive snake_case key name.

OUTPUT FORMAT (strict — no deviation):
{"facts": [{"key": str, "value": str, "confidence": float,
            "source_rank": "vet_record"|"user_correction"|"explicit_owner",
            "time_scope": "current"|"past"|"unknown", "uncertainty": str,
            "source_quote": str, "timestamp": str|null,
            "pet_label": "pet_a"|"pet_b"}]}"""


# ── ExtractedFact ───────────────────────────────────────────────────────────────

@dataclass
class ExtractedFact:
    """
    One structured fact extracted from a user message.

    Field meanings:
        key          — snake_case field name (from preferred taxonomy or freeform)
        value        — always a string; units normalized ("4 kilos" → "4 kg", key="weight")
        confidence   — 0.0–1.0; LLM-assigned based on language certainty
        source_rank  — "vet_record" | "user_correction" | "explicit_owner"
        time_scope   — "current" | "past" | "unknown"
        uncertainty  — plain text reason why confidence < 1.0, or "" if fully confident
        source_quote — exact substring from the user message that supports this fact
        timestamp    — ISO datetime string if user stated a specific time, else None
        pet_label    — "pet_a" or "pet_b"; identifies which pet this fact belongs to
                       Defaults to "pet_a" for backward compat with single-pet sessions.
    """
    key: str
    value: str
    confidence: float
    source_rank: str
    time_scope: str
    uncertainty: str
    source_quote: str
    timestamp: str | None
    pet_label: str = "pet_a"   # "pet_a" or "pet_b" — set by Compressor for dual-pet


# ── _parse_compressor_response ──────────────────────────────────────────────────

def _parse_compressor_response(raw: str) -> list[dict] | None:
    """
    Parse the Compressor's JSON output into a list of raw fact dicts.

    Strips markdown fences the LLM sometimes adds despite instructions,
    then parses and validates the top-level structure.

    Returns:
        List of raw dicts (not yet validated as ExtractedFact) if parsing succeeds.
        None if the response cannot be parsed at all.

    Callers should treat None as a parse failure and return [] from run().
    Individual fact dicts are validated separately in _build_facts().
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
        facts = data.get("facts")
        if not isinstance(facts, list):
            logger.warning(
                "Compressor: 'facts' key missing or not a list. raw[:80]=%r", raw[:80]
            )
            return None
        return facts
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning(
            "Compressor: JSON parse failed (%s). raw[:80]=%r", exc, raw[:80]
        )
        return None


# ── Schema validation constants (T1-04 Layer 2) ──────────────────────────────
#
# These protect against memory poisoning: a malicious message could trick the
# LLM into extracting fake facts with arbitrary keys/values. We validate every
# fact before it can reach the Aggregator and be written to the database.

# Key must be snake_case: starts with a letter, only a-z / 0-9 / underscore,
# max 100 chars. Rejects empty strings, keys starting with digits, injected
# SQL/HTML, and excessively long keys.
_VALID_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,99}$')

# Allowed source_rank values — anything else defaults to "explicit_owner".
_VALID_SOURCE_RANKS: frozenset[str] = frozenset({
    "vet_record", "user_correction", "explicit_owner",
})

# Maximum allowed length for a fact value. Prevents the LLM from writing
# unbounded text into the active_profile table.
_MAX_VALUE_LENGTH: int = 500


# ── _build_facts ────────────────────────────────────────────────────────────────

def _build_facts(raw_facts: list[dict], min_confidence: float) -> list[ExtractedFact]:
    """
    Convert raw dicts from the LLM into ExtractedFact objects.

    Skips entries that:
      - are missing required keys
      - have confidence below min_confidence
      - have non-numeric confidence values
      - have a key that does not match snake_case format (T1-04 schema check)
      - have a value longer than _MAX_VALUE_LENGTH chars (T1-04 schema check)

    Normalises source_rank to "explicit_owner" if the LLM returns an unknown
    value (T1-04 schema check).

    Logs a warning for each skipped or normalised entry.
    """
    results: list[ExtractedFact] = []

    for i, item in enumerate(raw_facts):
        try:
            confidence = float(item["confidence"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Compressor: fact[%d] has invalid confidence — skipped", i)
            continue

        if confidence < min_confidence:
            logger.debug(
                "Compressor: fact[%d] key=%r confidence=%.2f < threshold %.2f — discarded",
                i, item.get("key"), confidence, min_confidence,
            )
            continue

        try:
            key = str(item["key"])
            value = str(item["value"])

            # ── Schema check: key format (T1-04) ─────────────────────────────
            if not _VALID_KEY_RE.match(key):
                logger.warning(
                    "Compressor: fact[%d] key=%r failed schema check — skipped", i, key)
                continue

            # ── Schema check: value length (T1-04) ───────────────────────────
            if len(value) > _MAX_VALUE_LENGTH:
                logger.warning(
                    "Compressor: fact[%d] key=%r value too long (%d chars > %d) — skipped",
                    i, key, len(value), _MAX_VALUE_LENGTH,
                )
                continue

            # ── Schema check: source_rank allowlist (T1-04) ──────────────────
            raw_rank = str(item.get("source_rank", "explicit_owner"))
            source_rank = raw_rank if raw_rank in _VALID_SOURCE_RANKS else "explicit_owner"
            if source_rank != raw_rank:
                logger.warning(
                    "Compressor: fact[%d] key=%r unknown source_rank=%r — defaulting to explicit_owner",
                    i, key, raw_rank,
                )

            # Validate pet_label — must be "pet_a" or "pet_b", default "pet_a"
            raw_label = str(item.get("pet_label", "pet_a"))
            pet_label = raw_label if raw_label in ("pet_a", "pet_b") else "pet_a"

            # ── Schema check: time_scope allowlist (T1-04) ──────────────────
            # Normalize to lowercase so "Past" / "PAST" are caught by the
            # Aggregator's Rule 0 exact match (fact.time_scope == "past").
            raw_scope = str(item.get("time_scope", "unknown")).lower().strip()
            time_scope = raw_scope if raw_scope in {"current", "past", "unknown"} else "unknown"

            # ── Schema check: source_quote length cap (T1-04) ────────────────
            # source_quote is written to active_profile via _build_entry().
            # Cap at 200 chars — it is a short excerpt, not the full message.
            source_quote = str(item.get("source_quote", ""))[:200]

            fact = ExtractedFact(
                key=key,
                value=value,
                confidence=confidence,
                source_rank=source_rank,
                time_scope=time_scope,
                uncertainty=str(item.get("uncertainty", "")),
                source_quote=source_quote,
                timestamp=item.get("timestamp"),  # str or None
                pet_label=pet_label,
            )
            results.append(fact)
        except (KeyError, TypeError) as exc:
            logger.warning(
                "Compressor: fact[%d] malformed (%s) — skipped", i, exc)

    return results


# ── CompressorAgent ─────────────────────────────────────────────────────────────

class CompressorAgent:
    """
    Agent 2: extracts structured facts from user messages.

    Receives an LLMProvider via the constructor — same pattern as all agents.
    Never imports a concrete provider. Just calls self._llm.complete().

    Created once at startup in main.py and shared across all requests.
    """

    # Minimum confidence for a fact to be returned at all.
    # Facts below this are discarded completely (too speculative to be useful).
    _MIN_CONFIDENCE: float = 0.50

    # ── Model configuration ───────────────────────────────────────────────────
    # Model to use for this agent. None = use provider default (set in .env).
    # Change this to test a specific model, e.g. "gpt-5.4-nano".
    # See design-docs/model-strategy.md for full rationale.
    _MODEL: str | None = None

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        logger.info("CompressorAgent initialised.")

    # ── Public entry point ─────────────────────────────────────────────────────

    async def run(self, state: AgentState) -> list[ExtractedFact]:
        """
        Extract facts from state.user_message.

        Returns all ExtractedFact objects with confidence >= 0.50.
        Caller (main.py _run_background) is responsible for splitting by the
        0.70 threshold into high-confidence (→ Aggregator) and low-confidence
        (→ state.low_confidence_fields → clarification question next turn).

        Returns [] immediately if:
          - state.is_entity is False (ConversationAgent says no facts here)
          - LLM call fails
          - Response cannot be parsed

        Never raises — this runs inside asyncio.create_task() and must not
        crash the background task silently.
        """
        if not state.is_entity:
            logger.debug("Compressor: is_entity=False — skipping extraction.")
            return []

        user_prompt = self._build_user_prompt(state)

        try:
            raw = await self._llm.complete(
                system_prompt=COMPRESSOR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.0,   # deterministic — extraction, not generation
                max_tokens=600,    # enough for ~5 facts in JSON (dual-pet may return more)
                model=self._MODEL, # None = provider default; set above to test a model
            )
        except LLMProviderError as exc:
            logger.error("Compressor: LLM call failed: %s", exc)
            return []

        raw_facts = _parse_compressor_response(raw)
        if raw_facts is None:
            return []

        facts = _build_facts(raw_facts, self._MIN_CONFIDENCE)

        logger.info(
            "Compressor: parsed=%d valid facts (confidence >= %.2f) from message %r",
            len(facts), self._MIN_CONFIDENCE, state.user_message[:60],
        )

        return facts

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def _build_user_prompt(self, state: AgentState) -> str:
        """
        Build the user-role message for the Compressor LLM call.

        Includes:
          - Pet A essentials (name, species, age, sex, weight)
          - Pet B essentials (if dual-pet session)
          - Recent conversation history (last 3 turns) — for pronoun resolution
          - The user message to extract facts from

        The system prompt (COMPRESSOR_SYSTEM_PROMPT) is sent separately.
        """
        # ── Pet A essentials (always present) ─────────────────────────────────
        pet_a = state.pets[0]
        pet_a_parts = [f"Pet A: {pet_a.name}"]
        if pet_a.species:
            pet_a_parts.append(f"Species: {pet_a.species}")
        if pet_a.age:
            pet_a_parts.append(f"Age: {pet_a.age}")
        if pet_a.sex:
            pet_a_parts.append(f"Sex: {pet_a.sex}")
        if pet_a.weight:
            pet_a_parts.append(f"Weight: {pet_a.weight}")
        pet_a_line = " | ".join(pet_a_parts)

        # ── Pet B essentials (only if dual-pet session) ───────────────────────
        pet_b_line = ""
        if state.is_dual_pet:
            pet_b = state.pets[1]
            pet_b_parts = [f"Pet B: {pet_b.name}"]
            if pet_b.species:
                pet_b_parts.append(f"Species: {pet_b.species}")
            if pet_b.age:
                pet_b_parts.append(f"Age: {pet_b.age}")
            if pet_b.sex:
                pet_b_parts.append(f"Sex: {pet_b.sex}")
            if pet_b.weight:
                pet_b_parts.append(f"Weight: {pet_b.weight}")
            pet_b_line = "\n" + " | ".join(pet_b_parts)

        # ── Recent conversation history (last 3 turns = 6 messages) ───────────
        # Labelled "for pronoun resolution only" — we do not want the LLM to
        # extract facts from the assistant's replies (those have no new facts).
        history_section = ""
        if state.recent_history:
            lines = []
            for msg in state.recent_history[-6:]:
                role = "User" if msg.get("role") == "user" else "Assistant"
                content = msg.get("content", "")
                lines.append(f"{role}: {content}")
            if lines:
                history_section = (
                    "\nRecent conversation context "
                    "(for pronoun resolution only — do not extract facts from this):\n"
                    + "\n".join(lines)
                    + "\n"
                )

        # ── Assemble ───────────────────────────────────────────────────────────
        return (
            f"{pet_a_line}"
            f"{pet_b_line}"
            f"{history_section}"
            f"\nMessage to extract from:\n\"{state.user_message}\""
        )
