# app/agents/suggested_questions.py
#
# Generates 10 suggested questions for ONE pet (per-pet redesign).
#
# Used by:
#   - Nightly job (primary): pre-generates questions for all active users, per pet
#   - Background regen: triggered by aggregator after high-confidence fact
#
# How it works:
#   1. Receives context for ONE pet (pet_id, language, pets_json, trusted_context_json, gap_list)
#   2. Builds a prompt tailored to that specific pet
#   3. Calls a cheap/fast LLM (gpt-4.1-nano or similar)
#   4. Parses the JSON response into 10 structured questions
#   5. Caller validates + patches failed slots + caches (keyed by pet_id)
#
# Design decisions (see design-docs/suggested-questions.md):
#   - Questions are FROM the user's perspective, sent TO the AI
#   - Gap list is silent topic guidance, never explicit data-collection
#   - temperature=0.9 — creative variety, not deterministic output
#   - max_tokens=800 — 10 short questions in JSON fit well under this
#   - Slot layout: 3 dedicated + 1 both per module (food/health/anymall)

import json
import logging
from typing import Any

from app.llm.base import LLMProvider, LLMProviderError
from app.services.question_generation.validator import get_char_limit

logger = logging.getLogger(__name__)


# ── System prompt ───────────────────────────────────────────────────────────

SUGGESTED_QUESTIONS_SYSTEM_PROMPT = """\
You are generating suggested starter questions for the home screen of AnyMall-chan, \
a pet care AI assistant.

Your job is to create 10 short questions that a pet owner might tap to start a conversation.

These questions are FROM the owner's perspective, sent TO the AI assistant. \
The owner taps a question chip and it becomes their first message.

RULES:
- Write in this language: {language}
- Questions can cover any pet topic: food, health, behavior, daily care, exercise, grooming
- Tone must feel calm, clear, supportive, and natural
- Questions must feel useful for a pet parent, not clinical
- Keep each question to one sentence only
- Respect this hard max character length: {max_chars}
- Length must still fit after pet names are inserted
- Avoid repeating recently shown questions listed below
- Only use trusted context provided below — do not invent facts about the pet
- If context is weak, write safe general questions instead
- Do not sound alarmist or imply a diagnosis
- Do not mention emergencies, prescriptions, treatment plans, or medication dosing
- Write as if the OWNER is speaking to an AI assistant
- "What exercise routine suits Leo?" is correct framing
- "Can you tell me Leo's activity level?" is NOT acceptable (sounds like data collection)

{gap_instruction}

Pet setup: {pet_setup}

Pet data:
{pets_json}

Trusted context from profile:
{trusted_context_json}

Recently shown questions (avoid repeating these):
{recent_questions}

SLOT LAYOUT — generate exactly 10 questions in this order:
Slots 0-2  (3 questions): module="food"    — dedicated to THIS pet (target = {this_pet_target})
Slot  3    (1 question):  module="food"    — covers both pets  (target = "both")
Slots 4-6  (3 questions): module="health"  — dedicated to THIS pet (target = {this_pet_target})
Slot  7    (1 question):  module="health"  — covers both pets  (target = "both")
Slot  8    (1 question):  module="anymall" — dedicated to THIS pet (target = {this_pet_target})
Slot  9    (1 question):  module="anymall" — covers both pets  (target = "both")

Return ONLY valid JSON — no markdown, no explanation. Use this exact format:
[
  {{"text": "...", "module": "food",    "target": "{this_pet_target}"}},
  {{"text": "...", "module": "food",    "target": "{this_pet_target}"}},
  {{"text": "...", "module": "food",    "target": "{this_pet_target}"}},
  {{"text": "...", "module": "food",    "target": "both"}},
  {{"text": "...", "module": "health",  "target": "{this_pet_target}"}},
  {{"text": "...", "module": "health",  "target": "{this_pet_target}"}},
  {{"text": "...", "module": "health",  "target": "{this_pet_target}"}},
  {{"text": "...", "module": "health",  "target": "both"}},
  {{"text": "...", "module": "anymall", "target": "{this_pet_target}"}},
  {{"text": "...", "module": "anymall", "target": "both"}}
]

Allowed values:
- module: food | health | anymall
- target: pet_a | pet_b | both
"""


# ── Gap list instruction builder ────────────────────────────────────────────

_GAP_INSTRUCTION = """\
You are given a gap_list of information we don't yet know about this pet.
Do NOT ask directly for these fields.
Use this list only as topic guidance: lean toward questions that a caring owner \
would naturally ask, which might happen to touch on those areas.

Gap list: {gap_list}"""

_NO_GAP_INSTRUCTION = """\
No significant gaps in the pet profile. Focus on questions relevant to the \
known context above."""


# ── Model configuration ─────────────────────────────────────────────────────
# Model to use for this agent. None = use provider default (set in .env).
# Change this to test a specific model, e.g. "gpt-5.4-nano".
# Creative-variety agent (temperature=0.9 for variety, not deterministic output).
_MODEL: str | None = None


# ── Agent class ─────────────────────────────────────────────────────────────

class SuggestedQuestionsAgent:
    """
    LLM-powered generator for 10 suggested home screen questions (per-pet).

    Generates 3 dedicated food + 1 food/both + 3 dedicated health + 1 health/both
    + 1 anymall/this_pet + 1 anymall/both in a single LLM call for ONE specific pet.
    Uses a cheap/fast model (configurable via model parameter).
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        logger.info("SuggestedQuestionsAgent initialised.")

    async def generate(
        self,
        language: str,
        pet_id: int,
        pets_json: str,
        trusted_context_json: str,
        gap_list: list[str] | None = None,
        recent_questions: list[str] | None = None,
        model: str | None = None,
        is_pet_b: bool = False,
    ) -> list[dict[str, str]]:
        """
        Generate 10 suggested questions for ONE specific pet.

        Args:
            language: Resolved language code (EN, JA, KO, etc.)
            pet_id: The single pet to generate questions for
            pets_json: JSON string of pet profile data (for this pet)
            trusted_context_json: JSON string of high-confidence active profile facts
            gap_list: List of field names we don't know yet (silent guidance)
            recent_questions: List of question texts shown in last 4 weeks
            model: Optional model override for the LLM call
            is_pet_b: True if this pet is the second pet in the user's list (target="pet_b")

        Returns:
            List of 10 question dicts: [{text, module, target}, ...]
            Returns [] on failure (caller patches with evergreen).
        """
        max_chars = get_char_limit(language)
        this_pet_target = "pet_b" if is_pet_b else "pet_a"
        pet_setup = f"Generating for 1 pet (target label: {this_pet_target})"

        # Build gap instruction
        if gap_list:
            gap_instruction = _GAP_INSTRUCTION.format(gap_list=", ".join(gap_list[:10]))
        else:
            gap_instruction = _NO_GAP_INSTRUCTION

        # Format recent questions
        recent_str = "None" if not recent_questions else "\n".join(
            f"- {q}" for q in recent_questions
        )

        system_prompt = SUGGESTED_QUESTIONS_SYSTEM_PROMPT.format(
            language=language,
            max_chars=max_chars,
            pet_setup=pet_setup,
            pets_json=pets_json,
            trusted_context_json=trusted_context_json,
            gap_instruction=gap_instruction,
            recent_questions=recent_str,
            this_pet_target=this_pet_target,
        )

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                raw = await self._llm.complete(
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": "Generate 10 suggested questions now."}],
                    temperature=0.9,
                    max_tokens=800,
                    model=model,
                )
                result = self._parse_response(raw)
                if result:
                    return result
                if attempt < max_attempts:
                    logger.warning(
                        "SuggestedQuestionsAgent: parse returned empty, retrying "
                        "(attempt %d/%d pet_id=%s lang=%s)",
                        attempt, max_attempts, pet_id, language,
                    )
            except LLMProviderError as exc:
                logger.error("SuggestedQuestionsAgent LLM call failed: %s", exc)
                return []
            except Exception as exc:
                logger.error("SuggestedQuestionsAgent unexpected error: %s", exc)
                return []

        logger.warning(
            "SuggestedQuestionsAgent: all %d attempts failed (pet_id=%s lang=%s) — caller uses evergreen",
            max_attempts, pet_id, language,
        )
        return []

    def _parse_response(self, raw: str) -> list[dict[str, str]]:
        """
        Parse the LLM response JSON into a list of 10 question dicts.

        Strips markdown fences if present. Returns [] on parse failure.
        """
        text = raw.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            questions = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("SuggestedQuestionsAgent JSON parse failed: %s — raw=%s", exc, text[:200])
            return []

        if not isinstance(questions, list):
            logger.warning("SuggestedQuestionsAgent expected list, got %s", type(questions).__name__)
            return []

        # Validate structure of each item
        valid = []
        for q in questions:
            if (
                isinstance(q, dict)
                and isinstance(q.get("text"), str)
                and isinstance(q.get("module"), str)
                and isinstance(q.get("target"), str)
            ):
                valid.append({
                    "text": q["text"].strip(),
                    "module": q["module"],
                    "target": q["target"],
                })
            else:
                logger.debug("SuggestedQuestionsAgent skipping malformed item: %r", q)

        if len(valid) != 10:
            logger.warning(
                "SuggestedQuestionsAgent expected 10 valid questions, got %d", len(valid),
            )
            return []

        return valid
