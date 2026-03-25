# app/agents/suggested_questions.py
#
# Generates 4 suggested starter questions for the AnyMall-chan home screen.
#
# Used by:
#   - Nightly job (primary): pre-generates questions for all active users
#   - /api/v1/setup endpoint: validates + serves the cached result
#
# How it works:
#   1. Receives pet context (profile, active_profile, gap_list)
#   2. Builds a prompt tailored to the pet(s)
#   3. Calls a cheap/fast LLM (gpt-4.1-nano or similar)
#   4. Parses the JSON response into structured questions
#   5. Caller validates + caches
#
# Design decisions (see design-docs/suggested-questions.md):
#   - Questions are FROM the user's perspective, sent TO the AI
#   - Gap list is silent topic guidance, never explicit data-collection
#   - temperature=0.9 — we want creative variety, not deterministic output
#   - max_tokens=400 — 4 short questions in JSON is well under this

import json
import logging
from typing import Any

from app.llm.base import LLMProvider, LLMProviderError
from app.services.question_validator import get_char_limit

logger = logging.getLogger(__name__)


# ── System prompt ───────────────────────────────────────────────────────────

SUGGESTED_QUESTIONS_SYSTEM_PROMPT = """\
You are generating suggested starter questions for the home screen of AnyMall-chan, \
a pet care AI assistant.

Your job is to create 4 short questions that a pet owner might tap to start a conversation.

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

{mix_instruction}

Return ONLY valid JSON — no markdown, no explanation. Use this exact format:
[
  {{"text": "...", "target": "pet_a", "reason_type": "known_context"}},
  {{"text": "...", "target": "pet_a", "reason_type": "known_context"}},
  {{"text": "...", "target": "pet_a", "reason_type": "evergreen"}},
  {{"text": "...", "target": "pet_a", "reason_type": "evergreen"}}
]

Allowed values:
- target: pet_a | pet_b | both
- reason_type: known_context | missing_context | evergreen
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


# ── Mix instructions ────────────────────────────────────────────────────────

_SINGLE_PET_MIX = "All 4 questions must be about this single pet."

_DUAL_PET_MIX = """\
Question mix for two pets:
- 1 question about pet_a (target: "pet_a")
- 1 question about pet_b (target: "pet_b")
- 1 question about both pets together (target: "both")
- 1 additional question based on strongest relevance or biggest context gap"""


# ── Model configuration ─────────────────────────────────────────────────────
# Model to use for this agent. None = use provider default (set in .env).
# Change this to test a specific model, e.g. "gpt-5.4-nano".
# This is a Fast-tier agent (structured JSON output, no creativity needed).
_MODEL: str | None = None


# ── Agent class ─────────────────────────────────────────────────────────────

class SuggestedQuestionsAgent:
    """
    LLM-powered generator for suggested home screen questions.

    Uses a cheap/fast model (configurable via model parameter).
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        logger.info("SuggestedQuestionsAgent initialised.")

    async def generate(
        self,
        language: str,
        pet_count: int,
        pets_json: str,
        trusted_context_json: str,
        gap_list: list[str] | None = None,
        recent_questions: list[str] | None = None,
        model: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Generate 4 suggested questions.

        Args:
            language: Resolved language code (EN, JA, KO, etc.)
            pet_count: 1 or 2
            pets_json: JSON string of pet profile data
            trusted_context_json: JSON string of high-confidence active profile facts
            gap_list: List of field names we don't know yet (silent guidance)
            recent_questions: List of question texts shown in last 4 weeks
            model: Optional model override for the LLM call

        Returns:
            List of 4 question dicts: [{text, target, reason_type}, ...]
            Returns [] on failure (caller falls back to evergreen).
        """
        max_chars = get_char_limit(language)
        pet_setup = "1 pet" if pet_count == 1 else "2 pets"

        # Build gap instruction
        if gap_list:
            gap_instruction = _GAP_INSTRUCTION.format(gap_list=", ".join(gap_list[:10]))
        else:
            gap_instruction = _NO_GAP_INSTRUCTION

        # Build mix instruction
        mix_instruction = _SINGLE_PET_MIX if pet_count == 1 else _DUAL_PET_MIX

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
            mix_instruction=mix_instruction,
        )

        try:
            raw = await self._llm.complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": "Generate 4 suggested questions now."}],
                temperature=0.9,
                max_tokens=400,
                model=model,
            )
            return self._parse_response(raw)

        except LLMProviderError as exc:
            logger.error("SuggestedQuestionsAgent LLM call failed: %s", exc)
            return []
        except Exception as exc:
            logger.error("SuggestedQuestionsAgent unexpected error: %s", exc)
            return []

    def _parse_response(self, raw: str) -> list[dict[str, str]]:
        """
        Parse the LLM response JSON into a list of question dicts.

        Strips markdown fences if present. Returns [] on parse failure.
        """
        text = raw.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
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
                and isinstance(q.get("target"), str)
                and isinstance(q.get("reason_type"), str)
            ):
                valid.append({
                    "text": q["text"].strip(),
                    "target": q["target"],
                    "reason_type": q["reason_type"],
                })
            else:
                logger.debug("SuggestedQuestionsAgent skipping malformed item: %r", q)

        if len(valid) != 4:
            logger.warning(
                "SuggestedQuestionsAgent expected 4 valid questions, got %d", len(valid),
            )
            return []

        return valid
