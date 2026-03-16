# debug_flow.py
#
# Standalone debug script — run from backend/ directory:
#   python debug_flow.py
#
# Tests the full pipeline:
#   1. IntentClassifier  — what intent does the LLM return?
#   2. System prompt     — what exact prompt does Agent 1 receive?
#   3. Agent 1 reply     — what does Agent 1 say?
#
# Run this while the server is stopped (it starts its own LLM connection).
# Delete this file when debugging is done.

import asyncio
import os
import sys

# Allow imports from backend/ root (same as main.py does)
sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings
from app.llm.factory import create_llm_provider
from app.agents.intent_classifier import IntentClassifier
from app.agents.conversation import ConversationAgent
from app.services.context_builder import build_pet_context

# Minimal dummy data for debug — replace with real AALDA data if needed
_DUMMY_PROFILE = {"name": "Node", "species": "dog", "breed": "Toy Poodle", "date_of_birth": "2025-06-01", "sex": "male", "pet_id": 143}
_ctx = build_pet_context(_DUMMY_PROFILE, {}, None)
ACTIVE_PROFILE = _ctx["active_profile"]
GAP_LIST = _ctx["gap_list"]
PET_SUMMARY = _ctx["pet_summary"]
RELATIONSHIP_CONTEXT = "New user — no relationship data yet."

DIVIDER = "-" * 60

TEST_MESSAGES = [
    "Luna has been vomiting since morning",
    "What should Luna eat for better energy?",
    "Luna seems happy today",
]


async def main() -> None:
    print(DIVIDER)
    print("AnyMall-chan pipeline debug")
    print(DIVIDER)

    llm = create_llm_provider(settings)
    classifier = IntentClassifier(llm=llm)
    agent = ConversationAgent(llm=llm)

    for message in TEST_MESSAGES:
        print(f"\nMESSAGE: {message!r}")
        print(DIVIDER)

        # ── Step 1: Intent classification ──────────────────────────────────
        intent_type, urgency = await classifier.classify(message)
        print(f"CLASSIFIER OUTPUT: intent={intent_type!r}  urgency={urgency!r}")

        # ── Step 2: Rendered system prompt ─────────────────────────────────
        pet_name = ACTIVE_PROFILE.get("name", {}).get("value", "your pet")
        system_prompt = agent._build_system_prompt(
            pet_name=pet_name,
            pet_summary=PET_SUMMARY,
            pet_history=PET_HISTORY,
            gap_list=GAP_LIST,
            relationship_context=RELATIONSHIP_CONTEXT,
            intent_type=intent_type,
            questions_asked_so_far=0,
        )
        print("\nSYSTEM PROMPT (last 600 chars — flag section + rules):")
        print(system_prompt[-600:])

        # ── Step 3: Agent 1 reply ───────────────────────────────────────────
        response = await agent.run(
            user_message=message,
            session_messages=[],
            pet_a_context=_ctx,
            pet_b_context=None,
            relationship_context=RELATIONSHIP_CONTEXT,
            intent_type=intent_type,
            questions_asked_so_far=0,
        )
        print(f"\nAGENT 1 REPLY:\n{response.message}")
        print(DIVIDER)


if __name__ == "__main__":
    asyncio.run(main())
