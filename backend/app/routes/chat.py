# app/routes/chat.py
#
# POST /chat — the core endpoint.
#
# What lives here:
#   - Pydantic request/response models (ChatRequest, ChatResponse, RedirectPayload)
#   - POST /chat route
#   - _run_background() — fire-and-forget Compressor + Aggregator pipeline
#
# Shared state (agents, sessions) is accessed via request.app.state,
# which is populated by lifespan() in main.py. No module-level globals.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import dataclasses
from datetime import datetime, timezone
import logging
from typing import Any

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# ── Our code ───────────────────────────────────────────────────────────────────
from app.agents.conversation import AgentResponse
from app.agents.state import AgentState
from app.services.guardrails import apply_guardrails
from app.services.deeplink import build_deeplink
from app.services.context_builder import build_context
from constants import INTENT_HEALTH, INTENT_FOOD, URGENCY_HIGH, URGENCY_MEDIUM
from app.storage.file_store import append_fact_log
from app.services.confidence_calculator import calculate_confidence_score, confidence_color

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Language detection ───────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """
    Detect if text is primarily Japanese based on Unicode character ranges.

    Checks for Hiragana (U+3040-309F), Katakana (U+30A0-30FF), and CJK
    Unified Ideographs (U+4E00-9FFF).  Returns "JA" if any Japanese character
    is found, "EN" otherwise.

    Used to select the correct language for gap-question hints in Agent 1's
    system prompt.  The LLM itself adapts reply language from the user's input,
    but the gap hints are built in code and need the right language key.
    """
    ja_count = sum(
        1 for c in text
        if '\u3040' <= c <= '\u309f'      # Hiragana
        or '\u30a0' <= c <= '\u30ff'       # Katakana
        or '\u4e00' <= c <= '\u9fff'       # CJK Unified Ideographs
    )
    return "JA" if ja_count >= 1 else "EN"


# ── Request / response models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """
    Body for POST /chat.
    session_id maintains conversation history across messages.
    Use a fixed string in Postman. Flutter generates a UUID per conversation.
    """
    message: str = Field(..., min_length=1, max_length=4000,
                         description="The user's message.")
    session_id: str = Field(..., min_length=1, max_length=128,
                            description="Unique ID for this conversation session.")


class RedirectPayload(BaseModel):
    """
    Deeplink payload included in ChatResponse when a health or food intent is detected.

    The mobile app reads this to navigate the user to the correct specialist module.
    In Phase 1, pet_summary is passed inline. Phase 2 replaces it with a Redis key.
    """
    module: str               # "health" | "food"
    deep_link: str            # URL to open (localhost simulator in Phase 1)
    pre_populated_query: str  # user's original message, pre-filled in the module
    pet_summary: str          # Luna's full context so the module needs no extra lookup
    urgency: str              # "high" | "medium" | "low"


class ChatResponse(BaseModel):
    """Body returned by POST /chat."""
    message: str
    redirect: RedirectPayload | None = None   # present only for health/food intents
    session_id: str
    questions_asked_count: int
    was_guardrailed: bool
    # ── Agent debug fields ─────────────────────────────────────────────────────
    # These expose Agent 1 + IntentClassifier internals so the UI can display them.
    # Agent 2 (Compressor) output is available via GET /debug/facts?session_id=...
    is_entity: bool       # Agent 1: did the user message contain extractable pet facts?
    asked_gap_question: bool = False  # Agent 1: did the reply ask a gap-filling question?
    intent_type: str      # IntentClassifier: "health" | "food" | "general"
    urgency: str          # IntentClassifier: "high" | "medium" | "low"
    # ── Confidence bar ────────────────────────────────────────────────────────
    confidence_score: int  # 0-100, how well AnyMall-chan knows the pet
    confidence_color: str  # "green" (80-100) | "yellow" (50-79) | "red" (0-49)


def _to_redirect_payload(deeplink) -> RedirectPayload:
    """Convert a DeeplinkPayload dataclass to the Pydantic RedirectPayload."""
    return RedirectPayload(
        module=deeplink.module,
        deep_link=deeplink.deep_link,
        pre_populated_query=deeplink.pre_populated_query,
        pet_summary=deeplink.pet_summary,
        urgency=deeplink.urgency,
    )


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, summary="Send a message to Agent 1")
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """
    Core endpoint for Phase 0.

    Flow:
      1. Retrieve or create the session message history.
      2. IntentClassifier.classify() — LLM: intent_type + urgency (health/food/general).
      3. Agent 1                     — build prompt from context + intent, call LLM.
      4. apply_guardrails()          — regex: strip blocked jargon + preachy phrases.
      5. build_deeplink()            — build redirect payload if health or food intent.
      6. Save both messages to session history.
      7. Return ChatResponse.
    """
    state_bag = request.app.state
    agent = state_bag.agent
    intent_classifier = state_bag.intent_classifier

    if agent is None or intent_classifier is None:
        raise HTTPException(status_code=503, detail="Agent not initialised yet.")

    sessions: dict = state_bag.sessions

    # ── 1. Session history ─────────────────────────────────────────────────────
    session_id = request_body.session_id
    if session_id not in sessions:
        sessions[session_id] = []
        logger.info("New session started: %s", session_id)

    session_messages = sessions[session_id]

    # ── 1b. Load pet context from in-memory profiles ─────────────────────────
    # Reads from app.state (loaded once at startup, updated in-place by Aggregator).
    active_profile, gap_list, pet_summary, pet_history, relationship_context = build_context(
        active_profile_raw=state_bag.active_profile,
        pet_profile=state_bag.pet_profile,
        user_profile=state_bag.user_profile,
    )

    # ── Confidence bar (pure arithmetic, sub-ms) ─────────────────────────────
    conf_score = calculate_confidence_score(active_profile, state_bag.pet_profile)
    conf_color = confidence_color(conf_score)

    # Build AgentState — shared context for the background pipeline.
    # Snapshot session_messages now (before this turn is appended).
    agent_state = AgentState(
        session_id=session_id,
        user_message=request_body.message,
        pet_name=active_profile.get("name", {}).get("value", ""),
        pet_species=active_profile.get("species", {}).get("value", ""),
        pet_age=active_profile.get("age", {}).get("value", ""),
        pet_sex=active_profile.get("sex", {}).get("value", ""),
        pet_weight=active_profile.get("weight", {}).get("value", ""),
        recent_history=list(session_messages),  # snapshot before this turn
    )

    # Track gap questions and redirect cooldowns per session.
    meta = state_bag.session_meta.setdefault(session_id, {
        "gap_questions_asked": 0,
        "redirect_turn_tracker": {},
    })
    questions_so_far = meta["gap_questions_asked"]

    # ── 2. Intent classification (LLM) ────────────────────────────────────────
    intent_type, urgency = await intent_classifier.classify(request_body.message)

    # ── 3. Agent 1 ─────────────────────────────────────────────────────────────
    agent_response: AgentResponse = await agent.run(
        user_message=request_body.message,
        session_messages=session_messages,
        active_profile=active_profile,
        gap_list=gap_list,
        pet_summary=pet_summary,
        pet_history=pet_history,
        relationship_context=relationship_context,
        intent_type=intent_type,
        questions_asked_so_far=questions_so_far,
        urgency=urgency,
        language_str=_detect_language(request_body.message),
    )

    # Propagate is_entity flag from Agent 1 to state so Compressor knows whether to run.
    agent_state.is_entity = agent_response.is_entity

    # Update gap question counter using the LLM's flag (not ? counting).
    if agent_response.asked_gap_question:
        meta["gap_questions_asked"] += 1

    # ── 4. Guardrails ──────────────────────────────────────────────────────────
    guardrail_result = apply_guardrails(agent_response.message)
    final_reply = guardrail_result.reply
    was_guardrailed = guardrail_result.was_modified

    # ── 4b. Build deeplink with urgency gating ────────────────────────────────
    # HIGH = always show redirect. MEDIUM = show with 3-message cooldown. LOW = never.
    MEDIUM_COOLDOWN = 3  # skip N messages after showing a medium redirect

    redirect_payload = None
    if intent_type in (INTENT_HEALTH, INTENT_FOOD):
        tracker = meta.setdefault("redirect_turn_tracker", {})
        current_turn = len(session_messages) // 2  # count user messages so far

        if urgency == URGENCY_HIGH:
            deeplink = build_deeplink(intent_type, urgency, request_body.message, pet_summary)
            if deeplink:
                redirect_payload = _to_redirect_payload(deeplink)

        elif urgency == URGENCY_MEDIUM:
            last_shown_turn = tracker.get("medium_last_shown")
            if last_shown_turn is None or (current_turn - last_shown_turn) > MEDIUM_COOLDOWN:
                deeplink = build_deeplink(intent_type, urgency, request_body.message, pet_summary)
                if deeplink:
                    redirect_payload = _to_redirect_payload(deeplink)
                    tracker["medium_last_shown"] = current_turn
        # LOW → no redirect

    # ── 5. Save to session history ─────────────────────────────────────────────
    sessions[session_id].append({"role": "user",      "content": request_body.message})
    sessions[session_id].append({"role": "assistant",  "content": final_reply})

    # ── 6. Fire-and-forget Compressor ──────────────────────────────────────────
    # Update state with the final reply and the full history (now including this turn).
    # Then launch Compressor in the background — user already has their reply.
    agent_state.agent_reply = final_reply
    agent_state.recent_history = list(sessions[session_id])
    asyncio.create_task(_run_background(agent_state, state_bag))

    # ── Log every request so you can see what happened in the terminal ─────────
    logger.info(
        "Chat complete — session=%s | intent=%s | urgency=%s | questions=%d | guardrailed=%s",
        session_id,
        intent_type,
        urgency,
        agent_response.questions_asked_count,
        was_guardrailed,
    )

    # ── 7. Return ──────────────────────────────────────────────────────────────
    return ChatResponse(
        message=final_reply,
        redirect=redirect_payload,
        session_id=session_id,
        questions_asked_count=agent_response.questions_asked_count,
        was_guardrailed=was_guardrailed,
        is_entity=agent_response.is_entity,
        asked_gap_question=agent_response.asked_gap_question,
        intent_type=intent_type,
        urgency=urgency,
        confidence_score=conf_score,
        confidence_color=conf_color,
    )


# ── Background pipeline ────────────────────────────────────────────────────────

async def _run_background(state: AgentState, state_bag: Any) -> None:
    """
    Fire-and-forget coroutine launched by asyncio.create_task() after /chat returns.

    Runs the Compressor, splits facts by confidence threshold, and persists to
    data/fact_log.json. User never waits for any of this.

    Never raises — any exception is caught and logged. A crash here must not
    affect the user-facing response that was already sent.
    """
    compressor = state_bag.compressor
    aggregator = state_bag.aggregator

    if compressor is None:
        return

    try:
        facts = await compressor.run(state)

        # Split by confidence threshold.
        # > 0.70 → high-confidence: Aggregator updates active_profile.
        # 0.50–0.70 → low-confidence: Agent 1 asks a clarification question next turn.
        high = [f for f in facts if f.confidence > 0.70]
        low  = [f for f in facts if 0.50 <= f.confidence <= 0.70]

        state.extracted_facts       = high
        state.low_confidence_fields = [f.key for f in low]

        if facts:
            to_log = [
                {
                    **dataclasses.asdict(f),
                    "needs_clarification": f.confidence <= 0.70,
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": state.session_id,
                }
                for f in facts
            ]
            append_fact_log(to_log)

        logger.info(
            "Compressor done — session=%s extracted=%d high=%d low=%d",
            state.session_id, len(facts), len(high), len(low),
        )

        # ── Aggregator — merge high-confidence facts into active_profile ──
        if high and aggregator is not None:
            await aggregator.run(high, state.session_id, state_bag.active_profile)

    except Exception as exc:
        logger.error(
            "Background pipeline failed — session=%s error=%s",
            state.session_id, exc,
        )
