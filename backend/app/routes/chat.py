# app/routes/chat.py
#
# POST /api/v1/chat — the core endpoint.
# GET  /api/v1/pets — list user's pets from AALDA.
# GET  /api/v1/confidence — confidence bar score.
#
# What lives here:
#   - Pydantic request/response models (ChatRequest, ChatResponse, RedirectPayload)
#   - POST /api/v1/chat route
#   - GET /api/v1/pets route (fetches from AALDA)
#   - GET /api/v1/confidence route
#   - _run_background() — fire-and-forget Compressor + Aggregator pipeline
#
# Auth: every request must include X-User-Code header.
# Shared state (agents, sessions) is accessed via request.app.state,
# which is populated by lifespan() in main.py. No module-level globals.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# ── Our code ───────────────────────────────────────────────────────────────────
from app.agents.conversation import AgentResponse
from app.agents.state import AgentState
from app.services.guardrails import apply_guardrails
from app.services.deeplink import build_deeplink
from app.services.context_builder import build_pet_context
from app.services.pet_fetcher import PetFetchError
from constants import (
    INTENT_HEALTH, INTENT_FOOD, URGENCY_HIGH, URGENCY_MEDIUM,
    THREAD_COMPACTION_THRESHOLD, THREAD_CONTEXT_WINDOW, THREAD_EXPIRY_HOURS,
)
from app.db.session import get_session
from app.db.repositories import ActiveProfileRepo, FactLogRepo, ThreadRepo, ThreadMessageRepo
from app.services.confidence_calculator import calculate_confidence_score, confidence_color

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


# ── Auth helper ───────────────────────────────────────────────────────────────

def _require_user_code(request: Request) -> str:
    """Extract X-User-Code header or raise 401."""
    user_code = request.headers.get("x-user-code")
    if not user_code:
        raise HTTPException(status_code=401, detail="Missing X-User-Code header")
    return user_code


# ── Language detection ───────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """
    Detect if text is primarily Japanese based on Unicode character ranges.

    Checks for Hiragana (U+3040-309F), Katakana (U+30A0-30FF), and CJK
    Unified Ideographs (U+4E00-9FFF).  Returns "JA" if any Japanese character
    is found, "EN" otherwise.
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
    Body for POST /api/v1/chat.

    session_id maintains conversation history across messages.
    Flutter generates a UUID per conversation.
    pet_ids identifies which pet(s) — 1 or 2.
    """
    message: str = Field(..., min_length=1, max_length=4000,
                         description="The user's message.")
    session_id: str = Field(..., min_length=1, max_length=128,
                            description="Unique ID for this conversation session.")
    pet_ids: list[int] = Field(
        ..., min_length=1, max_length=2,
        description="1 or 2 pet IDs to chat about.",
    )
    language: str = Field(
        default="auto",
        description="Language preference: 'EN', 'JA', or 'auto' (detect from message).",
    )


class RedirectDisplay(BaseModel):
    """How the client should render the redirect button."""
    label: str    # "Talk to Health Assistant" | "Talk to Food Specialist"
    style: str    # "urgent" (red) | "suggestion" (orange)


class RedirectContext(BaseModel):
    """Data the target module needs to function."""
    query: str        # user's original message, pre-filled in the module
    pet_id: int       # which pet, so the module can fetch its own data
    pet_summary: str  # full NL pet context — by design, module needs this


class RedirectPayload(BaseModel):
    """
    Redirect payload included in ChatResponse when a health or food intent is detected.

    Backend says WHAT (module + context), client decides HOW to navigate.
    No URLs — Flutter uses screen routes, React opens simulator pages.
    """
    module: str                # "health" | "food"
    urgency: str               # "high" | "medium"
    display: RedirectDisplay   # how to render the button
    context: RedirectContext    # data for the target module


class ChatResponse(BaseModel):
    """Body returned by POST /api/v1/chat."""
    status: str = "ok"
    message: str
    redirect: RedirectPayload | None = None   # present only for health/food intents
    session_id: str
    # ── Phase 2: Thread management ─────────────────────────────────────────────
    thread_id: str                # backend's thread UUID
    new_thread: bool = False      # True if a new 24h thread was created this request
    # ── Existing fields ────────────────────────────────────────────────────────
    questions_asked_count: int
    was_guardrailed: bool
    # ── Agent debug fields ─────────────────────────────────────────────────────
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
        urgency=deeplink.urgency,
        display=RedirectDisplay(
            label=deeplink.display_label,
            style=deeplink.display_style,
        ),
        context=RedirectContext(
            query=deeplink.query,
            pet_id=deeplink.pet_id,
            pet_summary=deeplink.pet_summary,
        ),
    )


# ── List user's pets ─────────────────────────────────────────────────────────

@router.get("/pets", summary="List user's pets from AALDA")
async def list_pets(request: Request) -> dict[str, Any]:
    """
    Fetch all pets for the user from the AALDA API.

    Requires X-User-Code header.
    Returns: {"status": "ok", "pets": [...]}
    """
    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher

    try:
        pets = await pet_fetcher.fetch_user_pets(user_code)
    except PetFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"status": "ok", "pets": pets}


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, summary="Send a message to Agent 1")
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """
    Core chat endpoint.

    Flow:
      1. Auth — extract X-User-Code header.
      2. Fetch pet data from AALDA (parallel for 2 pets).
      3. Thread boundary — resolve session_id → thread_id (24h windows).
      4. IntentClassifier — LLM: intent_type + urgency (health/food/general).
      5. Agent 1          — build prompt from context + intent, call LLM.
      6. apply_guardrails  — regex: strip blocked jargon + preachy phrases.
      7. build_deeplink    — build redirect payload if health or food intent.
      8. Save to session history + fire-and-forget background pipeline.
      9. Return ChatResponse.
    """
    user_code = _require_user_code(request)
    state_bag = request.app.state
    agent = state_bag.agent
    intent_classifier = state_bag.intent_classifier
    pet_fetcher = state_bag.pet_fetcher

    if agent is None or intent_classifier is None:
        raise HTTPException(status_code=503, detail="Agent not initialised yet.")

    sessions: dict = state_bag.sessions
    pet_ids = request_body.pet_ids

    # ── 1. Fetch pet data from AALDA (parallel for 2 pets) ────────────────────
    try:
        fetch_tasks = [pet_fetcher.fetch_pet_profile(user_code, pid) for pid in pet_ids]
        pet_results = await asyncio.gather(*fetch_tasks)
    except PetFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    pet_profiles = [r[0] for r in pet_results]       # list of pet_profile dicts
    aalda_facts_list = [r[1] for r in pet_results]    # list of aalda_facts dicts

    # ── 2. Load active_profiles from DB (per pet) ────────────────────────────
    active_profiles_raw: list[dict | None] = []
    async with get_session() as db_session:
        ap_repo = ActiveProfileRepo(db_session)
        for pid in pet_ids:
            raw = await ap_repo.read_all(pid)
            active_profiles_raw.append(raw)

    # ── 3. Build context for each pet ─────────────────────────────────────────
    pet_contexts = []
    for i, pet_profile in enumerate(pet_profiles):
        aalda_facts = aalda_facts_list[i]
        active_raw = active_profiles_raw[i]
        ctx = build_pet_context(pet_profile, aalda_facts, active_raw)
        pet_contexts.append(ctx)

    # Primary pet (index 0) used for thread lookup, confidence, deeplink
    primary_ctx = pet_contexts[0]
    primary_pet_id = pet_ids[0]
    primary_profile = pet_profiles[0]

    # Relationship context (default for now — no AALDA user API yet)
    relationship_context = "New user — no relationship data yet."

    # ── Confidence bar (pure arithmetic, sub-ms) ─────────────────────────────
    conf_score = calculate_confidence_score(primary_ctx["active_profile"], primary_profile)
    conf_color = confidence_color(conf_score)

    # ── 4. Thread boundary logic (Phase 2) ────────────────────────────────────
    session_id = request_body.session_id
    user_id = user_code

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    new_thread = False
    conversation_summary = ""

    async with get_session() as db_session:
        thread_repo = ThreadRepo(db_session)
        existing = await thread_repo.get_active(primary_pet_id)

        if existing and existing["expires_at"] > now_iso:
            thread_id = existing["thread_id"]
            conversation_summary = existing.get("compaction_summary") or ""
        else:
            if existing:
                await thread_repo.expire(existing["thread_id"])
                logger.info("Thread expired: %s", existing["thread_id"])

            prev = await thread_repo.get_latest_expired(primary_pet_id)
            if prev and prev.get("compaction_summary"):
                conversation_summary = prev["compaction_summary"]

            thread_id = str(uuid4())
            expires_at = (now_utc + timedelta(hours=THREAD_EXPIRY_HOURS)).isoformat()
            await thread_repo.create(
                thread_id=thread_id,
                pet_id=primary_pet_id,
                user_id=user_id,
                started_at=now_iso,
                expires_at=expires_at,
            )
            new_thread = True
            logger.info("New thread created: %s (session=%s)", thread_id, session_id)

    if thread_id not in sessions:
        sessions[thread_id] = []

    session_messages = sessions[thread_id]

    # ── Build AgentState — shared context for the background pipeline ─────────
    agent_state = AgentState(
        session_id=session_id,
        thread_id=thread_id,
        user_message=request_body.message,
        pet_id=primary_pet_id,
        pet_name=primary_ctx["active_profile"].get("name", {}).get("value", ""),
        pet_species=primary_ctx["active_profile"].get("species", {}).get("value", ""),
        pet_age=primary_ctx["active_profile"].get("age", {}).get("value", ""),
        pet_sex=primary_ctx["active_profile"].get("sex", {}).get("value", ""),
        pet_weight=primary_ctx["active_profile"].get("weight", {}).get("value", ""),
        recent_history=list(session_messages),
    )

    # Track gap questions and redirect cooldowns per thread.
    meta = state_bag.session_meta.setdefault(thread_id, {
        "gap_questions_asked": 0,
        "redirect_turn_tracker": {},
    })
    questions_so_far = meta["gap_questions_asked"]

    # ── 5. Intent classification (LLM) ──────────────────────────────────────
    intent_type, urgency = await intent_classifier.classify(request_body.message)

    # ── 6. Agent 1 ───────────────────────────────────────────────────────────
    pet_a_context = pet_contexts[0]
    pet_b_context = pet_contexts[1] if len(pet_contexts) > 1 else None

    agent_response: AgentResponse = await agent.run(
        user_message=request_body.message,
        session_messages=session_messages,
        pet_a_context=pet_a_context,
        pet_b_context=pet_b_context,
        relationship_context=relationship_context,
        intent_type=intent_type,
        urgency=urgency,
        questions_asked_so_far=questions_so_far,
        language_str=request_body.language if request_body.language != "auto" else _detect_language(request_body.message),
        conversation_summary=conversation_summary,
    )

    agent_state.is_entity = agent_response.is_entity

    if agent_response.asked_gap_question:
        meta["gap_questions_asked"] += 1

    # ── 7. Guardrails ────────────────────────────────────────────────────────
    guardrail_result = apply_guardrails(agent_response.message)
    final_reply = guardrail_result.reply
    was_guardrailed = guardrail_result.was_modified

    # ── 7b. Build deeplink with urgency gating ──────────────────────────────
    MEDIUM_COOLDOWN = 3

    redirect_payload = None
    pet_summary_primary = primary_ctx["pet_summary"]
    if intent_type in (INTENT_HEALTH, INTENT_FOOD):
        tracker = meta.setdefault("redirect_turn_tracker", {})
        current_turn = len(session_messages) // 2

        if urgency == URGENCY_HIGH:
            deeplink = build_deeplink(intent_type, urgency, request_body.message, pet_summary_primary, primary_pet_id)
            if deeplink:
                redirect_payload = _to_redirect_payload(deeplink)

        elif urgency == URGENCY_MEDIUM:
            last_shown_turn = tracker.get("medium_last_shown")
            if last_shown_turn is None or (current_turn - last_shown_turn) > MEDIUM_COOLDOWN:
                deeplink = build_deeplink(intent_type, urgency, request_body.message, pet_summary_primary, primary_pet_id)
                if deeplink:
                    redirect_payload = _to_redirect_payload(deeplink)
                    tracker["medium_last_shown"] = current_turn

    # ── 8. Save to session history (in-memory, keyed by thread_id) ──────────
    sessions[thread_id].append({"role": "user",      "content": request_body.message})
    sessions[thread_id].append({"role": "assistant",  "content": final_reply})

    # ── 9. Fire-and-forget Compressor ────────────────────────────────────────
    agent_state.agent_reply = final_reply
    agent_state.recent_history = list(sessions[thread_id])
    asyncio.create_task(_run_background(agent_state, state_bag))

    logger.info(
        "Chat complete — session=%s | intent=%s | urgency=%s | questions=%d | guardrailed=%s",
        session_id, intent_type, urgency, agent_response.questions_asked_count, was_guardrailed,
    )

    # ── 10. Return ───────────────────────────────────────────────────────────
    return ChatResponse(
        message=final_reply,
        redirect=redirect_payload,
        session_id=session_id,
        thread_id=thread_id,
        new_thread=new_thread,
        questions_asked_count=agent_response.questions_asked_count,
        was_guardrailed=was_guardrailed,
        is_entity=agent_response.is_entity,
        asked_gap_question=agent_response.asked_gap_question,
        intent_type=intent_type,
        urgency=urgency,
        confidence_score=conf_score,
        confidence_color=conf_color,
    )


# ── Confidence endpoint ────────────────────────────────────────────────────────

@router.get("/confidence", summary="Current confidence bar score")
async def get_confidence(request: Request, pet_id: int = 0) -> dict[str, Any]:
    """
    Returns the current confidence score and color for a specific pet.

    Requires X-User-Code header and pet_id query param.
    Called by the frontend on mount and after each chat response.
    """
    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher

    if pet_id == 0:
        raise HTTPException(status_code=400, detail="pet_id query parameter is required.")

    try:
        pet_profile, aalda_facts = await pet_fetcher.fetch_pet_profile(user_code, pet_id)
    except PetFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    async with get_session() as db_session:
        ap_repo = ActiveProfileRepo(db_session)
        active_raw = await ap_repo.read_all(pet_id)

    ctx = build_pet_context(pet_profile, aalda_facts, active_raw)
    score = calculate_confidence_score(ctx["active_profile"], pet_profile)
    color = confidence_color(score)

    return {
        "status": "ok",
        "confidence_score": score,
        "confidence_color": color,
    }


# ── Background pipeline ────────────────────────────────────────────────────────

async def _run_background(state: AgentState, state_bag: Any) -> None:
    """
    Fire-and-forget coroutine launched by asyncio.create_task() after /chat returns.

    Runs the Compressor, splits facts by confidence threshold, and persists to
    PostgreSQL fact_log table. User never waits for any of this.

    Never raises — any exception is caught and logged.
    """
    compressor = state_bag.compressor
    aggregator = state_bag.aggregator

    try:
        # ── Write-through: persist messages to PostgreSQL ────────────────
        now_iso = datetime.now(timezone.utc).isoformat()
        async with get_session() as db_session:
            msg_repo = ThreadMessageRepo(db_session)
            await msg_repo.append_batch([
                {
                    "thread_id": state.thread_id,
                    "role": "user",
                    "content": state.user_message,
                    "timestamp": now_iso,
                },
                {
                    "thread_id": state.thread_id,
                    "role": "assistant",
                    "content": state.agent_reply,
                    "timestamp": now_iso,
                },
            ])

        # ── Compaction trigger ──────────────────────────────────────────
        sessions = state_bag.sessions
        thread_messages = sessions.get(state.thread_id, [])
        if len(thread_messages) >= THREAD_COMPACTION_THRESHOLD:
            asyncio.create_task(_run_compaction(state.thread_id, state.pet_id, state_bag))

        # ── Compressor pipeline ─────────────────────────────────────────
        if compressor is None:
            return

        facts = await compressor.run(state)

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
            async with get_session() as db_session:
                repo = FactLogRepo(db_session)
                await repo.append(to_log, pet_id=state.pet_id)

        logger.info(
            "Compressor done — session=%s extracted=%d high=%d low=%d",
            state.session_id, len(facts), len(high), len(low),
        )

        # ── Aggregator — merge high-confidence facts into active_profile ──
        if high and aggregator is not None:
            # Load current active_profile from DB for this pet
            async with get_session() as db_session:
                ap_repo = ActiveProfileRepo(db_session)
                current_profile = await ap_repo.read_all(state.pet_id) or {}
            await aggregator.run(high, state.session_id, current_profile, pet_id=state.pet_id)

    except Exception as exc:
        logger.error(
            "Background pipeline failed — session=%s error=%s",
            state.session_id, exc,
        )


async def _run_compaction(thread_id: str, pet_id: int, state_bag: Any) -> None:
    """
    Fire-and-forget compaction task.

    When message count exceeds THREAD_COMPACTION_THRESHOLD, summarize older
    messages with an LLM, store the summary in threads.compaction_summary,
    and trim the in-memory list to THREAD_CONTEXT_WINDOW recent messages.
    """
    try:
        sessions = state_bag.sessions
        messages = sessions.get(thread_id, [])
        if len(messages) < THREAD_COMPACTION_THRESHOLD:
            return

        old_messages = messages[:-THREAD_CONTEXT_WINDOW]
        recent_messages = messages[-THREAD_CONTEXT_WINDOW:]

        async with get_session() as db_session:
            thread_repo = ThreadRepo(db_session)
            thread = await thread_repo.get_active(pet_id)
            existing_summary = thread.get("compaction_summary") if thread else None

        summarizer = state_bag.thread_summarizer
        new_summary = await summarizer.summarize(old_messages, existing_summary)

        async with get_session() as db_session:
            thread_repo = ThreadRepo(db_session)
            await thread_repo.update_compaction_summary(thread_id, new_summary)

        sessions[thread_id] = recent_messages

        logger.info(
            "Compaction done — thread=%s old=%d kept=%d",
            thread_id, len(old_messages), len(recent_messages),
        )

    except Exception as exc:
        logger.error("Compaction failed — thread=%s error=%s", thread_id, exc)
