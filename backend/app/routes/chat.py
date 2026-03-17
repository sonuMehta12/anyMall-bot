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
#
# Background pipeline (_run_background, _run_compaction) lives in background.py.
#
# Auth: every request must include X-User-Code header.
# Shared state (agents, sessions) is accessed via request.app.state,
# which is populated by lifespan() in main.py. No module-level globals.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# ── Our code ───────────────────────────────────────────────────────────────────
from app.agents.conversation import AgentResponse
from app.agents.state import AgentState, PetInfo
from app.services.guardrails import apply_guardrails
from app.services.deeplink import build_deeplink
from app.services.context_builder import build_pet_context
from app.services.pet_fetcher import PetFetchError
from constants import (
    INTENT_HEALTH, INTENT_FOOD, URGENCY_HIGH, URGENCY_MEDIUM,
    THREAD_CONTEXT_WINDOW, THREAD_EXPIRY_HOURS,
)
from app.db.session import get_session
from app.db.repositories import ActiveProfileRepo, ThreadRepo, UserRepo
from app.services.confidence_calculator import calculate_confidence_score, confidence_color

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


# ── Background pipeline (extracted to background.py) ──────────────────────────
from app.routes.background import _create_tracked_task, _run_background, _run_compaction


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
    return "JA" if ja_count >= 3 else "EN"


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
    try:
        async with get_session() as db_session:
            ap_repo = ActiveProfileRepo(db_session)
            for pid in pet_ids:
                raw = await ap_repo.read_all(pid)
                active_profiles_raw.append(raw)
    except Exception as db_exc:
        logger.error("DB error loading active profiles: %s", db_exc)
        raise HTTPException(status_code=503, detail="Database unavailable — please retry.")

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

    # ── Timestamp (used by user upsert, thread boundary, and message persistence) ──
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # ── Auto-upsert user record (W18 — creates on first visit) ──────────────
    user_record = None
    try:
        async with get_session() as db_session:
            user_repo = UserRepo(db_session)
            user_record = await user_repo.read(user_code)
            if not user_record:
                await user_repo.upsert({
                    "user_code": user_code,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                })
                user_record = await user_repo.read(user_code)
            else:
                # Return visit — update timestamp, preserve existing fields
                await user_repo.upsert({
                    "user_code": user_code,
                    "updated_at": now_iso,
                    "session_count": user_record.get("session_count", 0) + 1,
                    "relationship_summary": user_record.get("relationship_summary", ""),
                    "preferred_language": user_record.get("preferred_language", "auto"),
                })
    except Exception as user_exc:
        logger.warning("User upsert failed (non-fatal): %s", user_exc)

    # Relationship context — read from user record, fallback to default
    relationship_context = (user_record or {}).get("relationship_summary", "") \
        or "New user — no relationship data yet."

    # ── Confidence bar (pure arithmetic, sub-ms) ─────────────────────────────
    conf_score = calculate_confidence_score(primary_ctx["active_profile"], primary_profile)
    conf_color = confidence_color(conf_score)

    # ── 4. Thread boundary logic (Phase 2) ────────────────────────────────────
    session_id = request_body.session_id
    user_id = user_code
    new_thread = False
    conversation_summary = ""

    # Lock per primary pet to prevent duplicate thread creation from concurrent
    # requests (Phase 2 Addendum race fix). The DB also has a partial unique
    # index as a safety net (ix_threads_one_active_per_pet).
    pet_lock = state_bag.pet_locks.setdefault(primary_pet_id, asyncio.Lock())
    async with pet_lock:
        try:
            async with get_session() as db_session:
                thread_repo = ThreadRepo(db_session)
                existing = await thread_repo.get_active(primary_pet_id)

                if existing and datetime.fromisoformat(existing["expires_at"]) > now_utc:
                    thread_id = existing["thread_id"]
                    conversation_summary = existing.get("compaction_summary") or ""
                    # W11: set secondary_pet_id if upgrading a single-pet thread to dual-pet.
                    # Once set, it's immutable for this thread's lifetime (24h). If the user
                    # switches Pet B mid-thread, the old secondary stays — this is intentional
                    # because facts already logged to that pet_id would become orphaned.
                    secondary_pid = pet_ids[1] if len(pet_ids) > 1 else None
                    if secondary_pid and not existing.get("secondary_pet_id"):
                        await thread_repo.update_secondary_pet_id(thread_id, secondary_pid)
                else:
                    if existing:
                        await thread_repo.expire(existing["thread_id"])
                        # Clean up in-memory state for the expired thread (W4+W5)
                        sessions.pop(existing["thread_id"], None)
                        state_bag.session_meta.pop(existing["thread_id"], None)
                        state_bag.pending_clarifications.pop(existing["thread_id"], None)
                        logger.info("Thread expired: %s", existing["thread_id"])

                    prev = await thread_repo.get_latest_expired(primary_pet_id)
                    if prev and prev.get("compaction_summary"):
                        conversation_summary = prev["compaction_summary"]

                    thread_id = str(uuid4())
                    expires_at = (now_utc + timedelta(hours=THREAD_EXPIRY_HOURS)).isoformat()
                    secondary_pid = pet_ids[1] if len(pet_ids) > 1 else None
                    await thread_repo.create(
                        thread_id=thread_id,
                        pet_id=primary_pet_id,
                        user_id=user_id,
                        started_at=now_iso,
                        expires_at=expires_at,
                        secondary_pet_id=secondary_pid,
                    )
                    new_thread = True
                    logger.info("New thread created: %s (session=%s)", thread_id, session_id)
        except HTTPException:
            raise  # re-raise our own errors (shouldn't happen here, but defensive)
        except Exception as db_exc:
            logger.error("DB error in thread boundary: %s", db_exc)
            raise HTTPException(status_code=503, detail="Database unavailable — please retry.")

    # ── Acquire per-thread lock (C2 — prevent concurrent session mutations) ──
    thread_locks: dict[str, asyncio.Lock] = state_bag.thread_locks
    thread_lock = thread_locks.setdefault(thread_id, asyncio.Lock())

    async with thread_lock:
        if thread_id not in sessions:
            sessions[thread_id] = []

        session_messages = sessions[thread_id]

        # ── Build AgentState — shared context for the background pipeline ─────
        pet_infos = []
        for i, pid in enumerate(pet_ids):
            ap = pet_contexts[i]["active_profile"]
            pet_infos.append(PetInfo(
                id=pid,
                name=ap.get("name", {}).get("value", ""),
                species=ap.get("species", {}).get("value", ""),
                age=ap.get("age", {}).get("value", ""),
                sex=ap.get("sex", {}).get("value", ""),
                weight=ap.get("weight", {}).get("value", ""),
            ))

        agent_state = AgentState(
            session_id=session_id,
            thread_id=thread_id,
            user_message=request_body.message,
            pets=pet_infos,
            recent_history=list(session_messages),
        )

        # Track gap questions and redirect cooldowns per thread.
        meta = state_bag.session_meta.setdefault(thread_id, {
            "gap_questions_asked": 0,
            "redirect_turn_tracker": {},
        })
        questions_so_far = meta["gap_questions_asked"]

        # ── 5. Intent classification (LLM) ──────────────────────────────────
        intent_type, urgency = await intent_classifier.classify(request_body.message)

        # ── 6. Agent 1 ──────────────────────────────────────────────────────
        pet_a_context = pet_contexts[0]
        pet_b_context = pet_contexts[1] if len(pet_contexts) > 1 else None

        # Read pending clarifications for this thread (from previous turn's background pipeline)
        pending_store = getattr(state_bag, "pending_clarifications", {})
        pending_clars = pending_store.get(thread_id, [])

        agent_response: AgentResponse = await agent.run(
            user_message=request_body.message,
            session_messages=list(session_messages[-THREAD_CONTEXT_WINDOW:]),
            pet_a_context=pet_a_context,
            pet_b_context=pet_b_context,
            relationship_context=relationship_context,
            intent_type=intent_type,
            urgency=urgency,
            questions_asked_so_far=questions_so_far,
            language_str=request_body.language if request_body.language != "auto" else _detect_language(request_body.message),
            conversation_summary=conversation_summary,
            pending_clarifications=pending_clars or None,
        )

        agent_state.is_entity = agent_response.is_entity

        # W7: Reset gap counter when the user provides a fact (entity) after
        # we asked a gap question — the user is engaging, so we can ask more.
        if agent_response.is_entity and meta.get("last_asked_gap", False):
            meta["gap_questions_asked"] = 0

        meta["last_asked_gap"] = agent_response.asked_gap_question

        if agent_response.asked_gap_question:
            meta["gap_questions_asked"] += 1

        # ── 7. Guardrails ───────────────────────────────────────────────────
        guardrail_result = apply_guardrails(agent_response.message)
        final_reply = guardrail_result.reply
        was_guardrailed = guardrail_result.was_modified

        # ── 7b. Build deeplink with urgency gating ─────────────────────────
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

        # ── 8. Save to session history (in-memory, keyed by thread_id) ────
        sessions[thread_id].append({"role": "user",      "content": request_body.message, "timestamp": now_iso})
        sessions[thread_id].append({"role": "assistant",  "content": final_reply, "timestamp": now_iso})

        # ── 9. Fire-and-forget Compressor ─────────────────────────────────
        agent_state.agent_reply = final_reply
        agent_state.recent_history = list(sessions[thread_id])
        _create_tracked_task(_run_background(agent_state, state_bag), state_bag)

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
async def get_confidence(request: Request, pet_id: int | None = None) -> dict[str, Any]:
    """
    Returns the current confidence score and color for a specific pet.

    Requires X-User-Code header and pet_id query param.
    Called by the frontend on mount and after each chat response.
    """
    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher

    if pet_id is None:
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


