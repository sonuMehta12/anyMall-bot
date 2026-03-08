# app/agents/conversation.py
#
# Agent 1 — the conversational layer the user talks to.
#
# How this file is organised:
#   1. SYSTEM_PROMPT_TEMPLATE  — the full prompt as one readable string
#   2. RULES_TEXT              — the hard rules section (constant)
#   3. AgentResponse           — dataclass returned by run()
#   4. ConversationAgent       — the agent class
#        run()                 — public entry point called by main.py
#        _build_system_prompt()— fills in the template
#        _build_gap_section()  — builds the dynamic gap text
#        _build_flag_section() — builds the dynamic flag text

import json
import logging
from dataclasses import dataclass

from app.llm.base import LLMProvider, LLMProviderError
from constants import (
    MAX_QUESTIONS_PER_SESSION,
    MAX_QUESTIONS_PER_MESSAGE,
    GAP_QUESTION_HINTS,
    INTENT_HEALTH,
    INTENT_FOOD,
)

logger = logging.getLogger(__name__)


# ── Prompt template ────────────────────────────────────────────────────────────
#
# This is the FULL system prompt the LLM receives on every request.
# Read this and you know exactly what the LLM sees — no hunting through methods.
#
# Placeholders wrapped in {curly_braces} are filled in by _build_system_prompt().
# Only {gap_section} and {flag_section} need code logic to build.
# Everything else is a direct value substitution.

SYSTEM_PROMPT_TEMPLATE = """\
You are AnyMall-chan, a warm and knowledgeable pet companion AI.
You know {pet_name} well and speak about them naturally, \
like a trusted friend who also knows a lot about pet care.

ABOUT {pet_name_upper}:
{pet_summary}

{pet_name_upper}'S HISTORY:
{pet_history}

INFORMATION GAPS:
{gap_section}

HOW TO COMMUNICATE:
{relationship_context}
{flag_section}
RULES:
{rules}

OUTPUT FORMAT:
Reply with ONLY a valid JSON object — no markdown, no explanation, nothing else.
{{"reply": "your full conversational response here", "is_entity": true|false}}

is_entity rules:
- true  if the owner's message contains any extractable pet fact (weight, age, breed,
         diet, medical condition, medication, behavior trait, vet info, vaccination status).
- false for greetings, thanks, pure questions with no facts, short acknowledgements.
- When uncertain: set true. Missing a fact is worse than processing an empty message."""


# ── Rules text ─────────────────────────────────────────────────────────────────
#
# Hard behavioural constraints. Defined once here as a constant.
# If you want to add or change a rule, edit this string — one place, done.

RULES_TEXT = f"""\
1. Never claim to be a veterinarian or give a medical diagnosis.
2. Never use preachy or moralising language.
3. Ask at most {MAX_QUESTIONS_PER_MESSAGE} question per reply.
4. If you ask a gap-filling question, make it feel natural — not like a form.
5. Always respond in the same language the owner uses.
6. If the owner seems worried, be warm and reassuring first, informative second.
7. HEALTH REDIRECT: If "HEALTH INTENT DETECTED" appears in the flags above — give \
emotional warmth only. No advice, no diagnosis, no treatment suggestions. \
1–2 sentences maximum. Signal that help is on the way.
8. FOOD REDIRECT: If "FOOD INTENT DETECTED" appears in the flags above — one warm \
sentence only. For dietary guidance, direct the owner to the Food Specialist."""


# ── AgentResponse ──────────────────────────────────────────────────────────────

@dataclass
class AgentResponse:
    """
    What Agent 1 returns after processing one user message.

    The /chat route sends `message` to the user.
    `questions_asked_count` is metadata for the session question limit.
    """
    message: str
    questions_asked_count: int = 0
    was_guardrailed: bool = False
    is_entity: bool = False          # did this message contain extractable pet facts?


# ── _parse_agent_response ──────────────────────────────────────────────────────
#
# Agent 1 is instructed to output {"reply": "...", "is_entity": bool}.
# This function parses that JSON. On any failure it falls back safely:
#   - reply  → raw LLM text (user still gets a response, never lost)
#   - is_entity → True (never silently skip a message that might contain facts)

def _parse_agent_response(raw: str) -> tuple[str, bool]:
    """
    Parse Agent 1's JSON output into (reply_text, is_entity).

    Strips markdown fences the LLM sometimes adds despite instructions,
    then parses. On any failure returns (raw, True) as a safe fallback.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
        reply_text = str(data["reply"])
        is_entity = bool(data.get("is_entity", True))  # default True on missing key
        return reply_text, is_entity
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning(
            "Agent1: JSON parse failed — using raw output as reply, is_entity=True. "
            "raw[:80]=%r", raw[:80],
        )
        return raw, True


# ── ConversationAgent ──────────────────────────────────────────────────────────

class ConversationAgent:
    """
    Agent 1: the conversational AI that talks to pet owners.

    Receives an LLMProvider via the constructor.
    Never imports a concrete provider — only the abstract LLMProvider base.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        logger.info("ConversationAgent initialised.")

    # ── Public entry point ─────────────────────────────────────────────────────

    async def run(
        self,
        user_message: str,
        session_messages: list[dict[str, str]],
        active_profile: dict,
        gap_list: list[str],
        pet_summary: str,
        pet_history: str,
        relationship_context: str,
        intent_type: str,
        questions_asked_so_far: int = 0,
    ) -> AgentResponse:
        """
        Process one user message and return an AgentResponse.

        Args:
            user_message:          The latest message from the owner.
            session_messages:      Previous messages this session.
                                   Each: {"role": "user"/"assistant", "content": "..."}
            active_profile:        Structured dict of known facts + confidence scores.
                                   Used to read pet_name. Gap detection uses gap_list.
            gap_list:              Fields we do not know yet.
            pet_summary:           NL paragraph: who the pet is right now.
            pet_history:           NL paragraph: what happened to the pet over time.
            relationship_context:  NL sentence: owner's communication style.
            intent_type:           Output of IntentClassifier — "health", "food", or "general".
                                   Urgency is not passed — Agent 1 does not need it.
            questions_asked_so_far: Gap questions already asked this session.

        Returns:
            AgentResponse with the reply and metadata.
        """
        pet_name = active_profile.get("name", {}).get("value", "your pet")

        logger.info(
            "Agent1.run — pet=%s  gaps=%d  questions_so_far=%d",
            pet_name, len(gap_list), questions_asked_so_far,
        )

        system_prompt = self._build_system_prompt(
            pet_name=pet_name,
            pet_summary=pet_summary,
            pet_history=pet_history,
            gap_list=gap_list,
            relationship_context=relationship_context,
            intent_type=intent_type,
            questions_asked_so_far=questions_asked_so_far,
        )

        # Append the current message to history before sending to LLM
        messages = session_messages + \
            [{"role": "user", "content": user_message}]

        try:
            raw = await self._llm.complete(
                system_prompt=system_prompt,
                messages=messages,
                temperature=0.7,
                max_tokens=512,
            )
        except LLMProviderError as exc:
            logger.error("LLM call failed: %s", exc)
            return AgentResponse(
                message="Sorry, I'm having trouble connecting right now. Please try again in a moment!",
                questions_asked_count=questions_asked_so_far,
            )

        # ── Parse JSON output ──────────────────────────────────────────────────
        # Agent 1 is instructed to output {"reply": "...", "is_entity": bool}.
        # On parse failure: raw text as reply, is_entity=True (safe fallback).
        reply_text, is_entity = _parse_agent_response(raw)

        # Count questions in this reply (cap at 1 — rules say max 1 question)
        questions_this_turn = min(reply_text.count("?"), 1)
        total_questions = questions_asked_so_far + questions_this_turn

        logger.info(
            "Agent1 reply — length=%d chars  questions_this_turn=%d  is_entity=%s",
            len(reply_text), questions_this_turn, is_entity,
        )

        return AgentResponse(
            message=reply_text,
            questions_asked_count=total_questions,
            was_guardrailed=False,  # guardrails applied in main.py after this
            is_entity=is_entity,
        )

    # ── System prompt builder ──────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        pet_name: str,
        pet_summary: str,
        pet_history: str,
        gap_list: list[str],
        relationship_context: str,
        intent_type: str,
        questions_asked_so_far: int,
    ) -> str:
        """
        Fill in SYSTEM_PROMPT_TEMPLATE with the current context.

        Only {gap_section} and {flag_section} need code to compute.
        Everything else is a direct substitution.
        """
        gap_section = self._build_gap_section(
            gap_list, pet_name, questions_asked_so_far)
        flag_section = self._build_flag_section(intent_type)

        return SYSTEM_PROMPT_TEMPLATE.format(
            pet_name=pet_name,
            pet_name_upper=pet_name.upper(),
            pet_summary=pet_summary,
            pet_history=pet_history or "No history yet — this is the first session.",
            gap_section=gap_section,
            relationship_context=relationship_context,
            flag_section=flag_section,
            rules=RULES_TEXT,
        )

    # ── Section builders ───────────────────────────────────────────────────────

    def _build_gap_section(
        self,
        gap_list: list[str],
        pet_name: str,
        questions_asked_so_far: int,
    ) -> str:
        """
        Build the INFORMATION GAPS section of the prompt.

        If gaps exist and questions remain, surfaces the first gap with a hint.
        If the session question limit is reached, tells the LLM to stop asking.
        """
        if not gap_list:
            return f"None — you have a complete profile for {pet_name}."

        missing_str = ", ".join(gap_list)
        lines = [f"Missing fields: {missing_str}"]

        remaining = MAX_QUESTIONS_PER_SESSION - questions_asked_so_far

        if remaining > 0:
            for gap_field in gap_list:
                if gap_field in GAP_QUESTION_HINTS:
                    hint = GAP_QUESTION_HINTS[gap_field].format(name=pet_name)
                    lines.append(
                        f"If it feels natural, you MAY ask about: {hint}. "
                        f"Ask at most ONE question. Do not list all gaps."
                    )
                    break
        else:
            lines.append(
                f"You have asked {questions_asked_so_far} questions this session. "
                f"Do NOT ask any more gap-filling questions."
            )

        return "\n".join(lines)

    def _build_flag_section(self, intent_type: str) -> str:
        """
        Build the THIS MESSAGE FLAGS section of the prompt.

        Returns an empty string for general messages — the template handles
        this gracefully (the section just disappears from the prompt).

        The LLM classifier (IntentClassifier) already determined intent_type
        and urgency. We just translate that into an instruction for Agent 1.
        """
        if intent_type == INTENT_HEALTH:
            return (
                "\nTHIS MESSAGE FLAGS:\n"
                "HEALTH INTENT DETECTED — Empathy only. No advice, no diagnosis, "
                "no treatment suggestions. 1–2 sentences maximum. "
                "Signal that help is on the way.\n"
            )

        if intent_type == INTENT_FOOD:
            return (
                "\nTHIS MESSAGE FLAGS:\n"
                "FOOD INTENT DETECTED — One warm sentence only. "
                "For dietary guidance, direct the owner to the Food Specialist.\n"
            )

        # General intent — no special instructions needed. Agent 1 responds naturally.
        return ""
