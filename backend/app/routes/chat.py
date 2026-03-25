# app/routes/chat.py
#
# POST /api/v1/chat — the core endpoint.
# GET  /api/v1/pets — list user's pets from AALDA.
# GET  /api/v1/setup — confidence bar + suggested questions (replaces /confidence).
# GET  /api/v1/confidence — alias for /setup (backward compat).
#
# What lives here:
#   - Pydantic request/response models (ChatRequest, ChatResponse, RedirectPayload)
#   - POST /api/v1/chat route
#   - GET /api/v1/pets route (fetches from AALDA)
#   - GET /api/v1/setup route (confidence + suggested questions)
#   - GET /api/v1/confidence route (alias for /setup)
#
# Background pipeline (_run_background, _run_compaction) lives in background.py.
#
# Auth: every request must include X-User-Code header.
# Shared state (agents, sessions) is accessed via request.app.state,
# which is populated by lifespan() in main.py. No module-level globals.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List
from uuid import uuid4

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

# ── Our code ───────────────────────────────────────────────────────────────────
from app.routes.background import _create_tracked_task, _run_background
from app.agents.conversation import AgentResponse
from app.agents.state import AgentState, PetInfo
from app.services.guardrails import apply_guardrails
from app.services.deeplink import build_deeplink
from app.services.context_builder import build_pet_context
from app.services.pet_fetcher import PetFetchError
from app.cache.client import ValkeyClient
from app.cache.keys import (
    CacheKeys, TTL_SESSION, TTL_META, TTL_PENDING, TTL_USER, TTL_PROFILE,
    TTL_SUGGESTED, TTL_SUGGESTED_HISTORY, jittered_ttl,
)
from constants import (
    INTENT_HEALTH, INTENT_FOOD, URGENCY_HIGH, URGENCY_MEDIUM,
    THREAD_CONTEXT_WINDOW, THREAD_EXPIRY_HOURS,
)
from app.db.session import get_session
from app.db.repositories import ActiveProfileRepo, ThreadRepo, ThreadMessageRepo, UserRepo
from app.services.confidence_calculator import calculate_confidence_score, confidence_color
from app.services.question_templates import get_evergreen_questions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


# ── Background pipeline (extracted to background.py) ──────────────────────────


# ── Auth helper ───────────────────────────────────────────────────────────────

def _require_user_code(request: Request) -> str:
    """Extract X-User-Code header or raise 401."""
    user_code = request.headers.get("x-user-code")
    if not user_code:
        raise HTTPException(
            status_code=401, detail="Missing X-User-Code header")
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
    display_name: str = Field(
        default="",
        max_length=128,
        description="Owner's display name from Flutter UI.",
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
    # present only for health/food intents
    redirect: RedirectPayload | None = None
    session_id: str
    # ── Phase 2: Thread management ─────────────────────────────────────────────
    thread_id: str                # backend's thread UUID
    new_thread: bool = False      # True if a new 24h thread was created this request
    # ── Existing fields ────────────────────────────────────────────────────────
    questions_asked_count: int
    was_guardrailed: bool
    # ── Agent debug fields ─────────────────────────────────────────────────────
    is_entity: bool       # Agent 1: did the user message contain extractable pet facts?
    # Agent 1: did the reply ask a gap-filling question?
    asked_gap_question: bool = False
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
        raise HTTPException(
            status_code=503, detail="Agent not initialised yet.")

    sessions: dict = state_bag.sessions
    pet_ids = request_body.pet_ids

    # ── 1. Fetch pet data from AALDA (parallel for 2 pets) ────────────────────
    try:
        fetch_tasks = [pet_fetcher.fetch_pet_profile(
            user_code, pid) for pid in pet_ids]
        pet_results = await asyncio.gather(*fetch_tasks)
    except PetFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    pet_profiles = [r[0]
                    for r in pet_results]       # list of pet_profile dicts
    aalda_facts_list = [r[1]
                        for r in pet_results]    # list of aalda_facts dicts

    # ── 2. Load active_profiles — Valkey cache-aside (ft-005, Step 6) ────────
    # Check Valkey for all pets first (before opening any DB session) so cache
    # hits don't waste a connection pool slot. Only open the DB session for the
    # subset of pet_ids that missed the cache.
    vk: ValkeyClient = state_bag.valkey
    active_profiles_raw: list[dict | None] = [None] * len(pet_ids)
    pids_to_fetch: list[tuple[int, int]] = []  # (list-index, pet_id)

    for i, pid in enumerate(pet_ids):
        raw_cached = await vk.get(CacheKeys.profile(pid))
        if raw_cached is not None:
            logger.debug("Profile cache hit — pet_id=%d", pid)
            active_profiles_raw[i] = json.loads(raw_cached)
        else:
            pids_to_fetch.append((i, pid))

    if pids_to_fetch:
        db_hits: list[tuple[int, int, dict]] = []  # (index, pet_id, profile)
        try:
            async with get_session() as db_session:
                ap_repo = ActiveProfileRepo(db_session)
                for idx, pid in pids_to_fetch:
                    raw = await ap_repo.read_all(pid)
                    active_profiles_raw[idx] = raw
                    if raw:
                        db_hits.append((idx, pid, raw))
        except Exception as db_exc:
            logger.error("DB error loading active profiles: %s", db_exc)
            raise HTTPException(
                status_code=503, detail="Database unavailable — please retry.")
        # Populate Valkey after the DB session closes (no connection held during cache write)
        for _, pid, raw in db_hits:
            await vk.setex(CacheKeys.profile(pid), jittered_ttl(TTL_PROFILE), json.dumps(raw))

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

    # ── Confidence bar (pure arithmetic, sub-ms) ─────────────────────────────
    # Average across all selected pets — single pet = no change, dual pet = averaged
    _conf_scores = [
        calculate_confidence_score(
            pet_contexts[i]["active_profile"], pet_profiles[i])
        for i in range(len(pet_contexts))
    ]
    conf_score = round(sum(_conf_scores) / len(_conf_scores))
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
                    conversation_summary = existing.get(
                        "compaction_summary") or ""
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
                        # Clean up expired thread from Valkey + local fallback dicts (W4+W5)
                        old_tid = existing["thread_id"]
                        await vk.delete(
                            CacheKeys.session(old_tid),
                            CacheKeys.meta(old_tid),
                            CacheKeys.pending(old_tid),
                        )
                        sessions.pop(old_tid, None)
                        state_bag.session_meta.pop(old_tid, None)
                        state_bag.pending_clarifications.pop(old_tid, None)
                        logger.info("Thread expired: %s", old_tid)

                    prev = await thread_repo.get_latest_expired(primary_pet_id)
                    if prev and prev.get("compaction_summary"):
                        conversation_summary = prev["compaction_summary"]

                    thread_id = str(uuid4())
                    expires_at = (
                        now_utc + timedelta(hours=THREAD_EXPIRY_HOURS)).isoformat()
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
                    logger.info("New thread created: %s (session=%s)",
                                thread_id, session_id)
        except HTTPException:
            # re-raise our own errors (shouldn't happen here, but defensive)
            raise
        except Exception as db_exc:
            logger.error("DB error in thread boundary: %s", db_exc)
            raise HTTPException(
                status_code=503, detail="Database unavailable — please retry.")

    # ── Auto-upsert user record — Valkey cache-aside + write-through (ft-005) ─
    # Placed after thread boundary so new_thread is known — session_count
    # increments only once per 24-hour thread window, not once per message.
    # vk.setex calls happen OUTSIDE the DB session to avoid holding a pool
    # connection across a Valkey round-trip.
    user_record = None
    try:
        raw_user = await vk.get(CacheKeys.user(user_code))
        if raw_user is not None:
            user_record = json.loads(raw_user)
            logger.debug("User cache hit — user_code=%s", user_code)

        vk_user_to_cache = None
        async with get_session() as db_session:
            user_repo = UserRepo(db_session)
            if user_record is None:
                user_record = await user_repo.read(user_code)

            if not user_record:
                await user_repo.upsert({
                    "user_code": user_code,
                    "display_name": request_body.display_name,
                    "preferred_language": request_body.language if request_body.language != "auto" else "auto",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                })
                user_record = await user_repo.read(user_code)
                if user_record:
                    vk_user_to_cache = user_record
            else:
                # Only overwrite display_name if Flutter sent one (non-empty).
                new_display = request_body.display_name or user_record.get(
                    "display_name", "")
                # Only overwrite preferred_language if request is explicit (not "auto").
                new_lang = (
                    request_body.language
                    if request_body.language != "auto"
                    else user_record.get("preferred_language", "auto")
                )
                updated = {
                    "user_code": user_code,
                    "display_name": new_display,
                    "updated_at": now_iso,
                    # Increment only when a new 24-hour thread window opens.
                    "session_count": user_record.get("session_count", 0) + (1 if new_thread else 0),
                    "relationship_summary": user_record.get("relationship_summary", ""),
                    "preferred_language": new_lang,
                }
                await user_repo.upsert(updated)
                user_record = {**user_record, **updated}
                vk_user_to_cache = user_record

        # Write-through to Valkey after DB session closes (no connection held)
        if vk_user_to_cache is not None:
            await vk.setex(
                CacheKeys.user(user_code), jittered_ttl(
                    TTL_USER), json.dumps(vk_user_to_cache)
            )
    except Exception as user_exc:
        logger.warning("User upsert failed (non-fatal): %s", user_exc)

    relationship_context = (user_record or {}).get("relationship_summary", "") \
        or "New user — no relationship data yet."

    # ── Acquire per-thread lock (C2 — prevent concurrent session mutations) ──
    thread_locks: dict[str, asyncio.Lock] = state_bag.thread_locks
    thread_lock = thread_locks.setdefault(thread_id, asyncio.Lock())

    async with thread_lock:
        # ── Load session messages — Valkey cache-aside (ft-005, Step 4) ─────
        # GET from Valkey first.  On miss → DB → populate Valkey.
        raw_session = await vk.get(CacheKeys.session(thread_id))
        if raw_session is not None:
            logger.debug("Session cache hit — thread_id=%s", thread_id)
            session_messages = json.loads(raw_session)
        else:
            # Cache miss → load from DB (or start empty for new threads)
            try:
                async with get_session() as db_session:
                    msg_repo = ThreadMessageRepo(db_session)
                    session_messages = await msg_repo.read_thread(thread_id)
            except Exception as exc:
                logger.warning(
                    "Session DB load failed — thread=%s: %s", thread_id, exc)
                session_messages = []
            # Populate Valkey so next request is a cache hit
            if session_messages:
                await vk.setex(
                    CacheKeys.session(thread_id),
                    jittered_ttl(TTL_SESSION),
                    json.dumps(session_messages),
                )
        # Keep local ref in sessions dict (used by _run_background for continuity)
        sessions[thread_id] = session_messages

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
            user_code=user_code,
            user_message=request_body.message,
            pets=pet_infos,
            recent_history=list(session_messages),
        )

        # ── Load session meta — Valkey cache-aside (ft-005, Step 5) ─────────
        # Tracks gap questions asked and redirect cooldowns per thread.
        # Acceptable to lose on Valkey down: counter resets to 0 (asks 1 extra question).
        raw_meta = await vk.get(CacheKeys.meta(thread_id))
        if raw_meta is not None:
            meta = json.loads(raw_meta)
        else:
            meta = state_bag.session_meta.get(thread_id) or {
                "gap_questions_asked": 0,
                "last_asked_gap": False,
                "redirect_turn_tracker": {},
            }
        questions_so_far = meta["gap_questions_asked"]

        # ── 5. Intent classification (LLM) ──────────────────────────────────
        intent_type, urgency = await intent_classifier.classify(request_body.message)

        # ── 6. Agent 1 ──────────────────────────────────────────────────────
        pet_a_context = pet_contexts[0]
        pet_b_context = pet_contexts[1] if len(pet_contexts) > 1 else None

        # ── Load pending clarifications — Valkey GET (ft-005, Step 5) ───────
        # Written by background pipeline after Compressor finds low-confidence facts.
        # Read next turn to inject hedged facts into Agent 1's prompt.
        # Acceptable to lose on Valkey down: returns [] (no clarification this turn).
        raw_pending = await vk.get(CacheKeys.pending(thread_id))
        if raw_pending is not None:
            pending_clars = json.loads(raw_pending)
        else:
            # Local dict fallback when Valkey is down
            pending_clars = state_bag.pending_clarifications.get(thread_id, [])

        # ── Language priority: request explicit > DB stored > auto-detect ────
        if request_body.language != "auto":
            language_str = request_body.language
        else:
            db_lang = (user_record or {}).get("preferred_language", "auto")
            language_str = db_lang if db_lang != "auto" else _detect_language(
                request_body.message)

        agent_response: AgentResponse = await agent.run(
            user_message=request_body.message,
            session_messages=list(session_messages[-THREAD_CONTEXT_WINDOW:]),
            pet_a_context=pet_a_context,
            pet_b_context=pet_b_context,
            relationship_context=relationship_context,
            intent_type=intent_type,
            urgency=urgency,
            questions_asked_so_far=questions_so_far,
            language_str=language_str,
            conversation_summary=conversation_summary,
            pending_clarifications=pending_clars or None,
            owner_name=(user_record or {}).get("display_name", ""),
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
                deeplink = build_deeplink(
                    intent_type, urgency, request_body.message, pet_summary_primary, primary_pet_id)
                if deeplink:
                    redirect_payload = _to_redirect_payload(deeplink)

            elif urgency == URGENCY_MEDIUM:
                last_shown_turn = tracker.get("medium_last_shown")
                if last_shown_turn is None or (current_turn - last_shown_turn) > MEDIUM_COOLDOWN:
                    deeplink = build_deeplink(
                        intent_type, urgency, request_body.message, pet_summary_primary, primary_pet_id)
                    if deeplink:
                        redirect_payload = _to_redirect_payload(deeplink)
                        tracker["medium_last_shown"] = current_turn

        # Write meta once after all mutations (gap counter + redirect tracker)
        await vk.setex(CacheKeys.meta(thread_id), jittered_ttl(TTL_META), json.dumps(meta))
        state_bag.session_meta[thread_id] = meta

        # ── 8. Save to session history ────────────────────────────────────────
        # Append to local dict so the next request has the data immediately
        # (Valkey cache miss falls back to sessions[thread_id]).
        # Background pipeline writes DB first, then appends to Valkey atomically
        # via LUA_APPEND_MESSAGES — preserving the DB-first write-through rule.
        user_msg = {"role": "user",
                    "content": request_body.message, "timestamp": now_iso}
        asst_msg = {"role": "assistant",
                    "content": final_reply, "timestamp": now_iso}
        sessions[thread_id].append(user_msg)
        sessions[thread_id].append(asst_msg)

        # ── 9. Fire-and-forget Compressor ─────────────────────────────────
        agent_state.agent_reply = final_reply
        agent_state.recent_history = list(sessions[thread_id])
        _create_tracked_task(_run_background(
            agent_state, state_bag), state_bag)

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


# ── Setup endpoint (confidence + suggested questions) ────────────────────────

@router.get("/setup", summary="Confidence bar + suggested questions")
async def get_setup(
    request: Request,
    pet_id: List[int] = Query(default=[]),
    language: str = Query(default="auto"),
) -> dict[str, Any]:
    """
    Returns confidence score + suggested home screen questions.

    Single pet:  GET /setup?pet_id=101
    Dual pet:    GET /setup?pet_id=101&pet_id=102

    Requires X-User-Code header. Called by the frontend on mount.
    Replaces the old /confidence endpoint with additional question data.
    """
    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher
    vk: ValkeyClient = request.app.state.valkey

    if not pet_id:
        raise HTTPException(
            status_code=400, detail="pet_id query parameter is required.")

    # ── 1. Confidence scoring (unchanged logic) ─────────────────────────────
    scores: list[int] = []
    pet_contexts: list[dict] = []
    pet_profiles: list[dict] = []

    for pid in pet_id:
        try:
            pet_profile, aalda_facts = await pet_fetcher.fetch_pet_profile(user_code, pid)
        except PetFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        pet_profiles.append(pet_profile)

        # Active profile — Valkey cache-aside
        raw_cached = await vk.get(CacheKeys.profile(pid))
        if raw_cached is not None:
            active_raw = json.loads(raw_cached)
        else:
            try:
                async with get_session() as db_session:
                    ap_repo = ActiveProfileRepo(db_session)
                    active_raw = await ap_repo.read_all(pid)
            except Exception as db_exc:
                logger.error("DB error in /setup — pet_id=%d: %s", pid, db_exc)
                raise HTTPException(
                    status_code=503, detail="Database unavailable — please retry.")
            if active_raw:
                await vk.setex(CacheKeys.profile(pid), jittered_ttl(TTL_PROFILE), json.dumps(active_raw))

        ctx = build_pet_context(pet_profile, aalda_facts, active_raw)
        pet_contexts.append(ctx)
        scores.append(calculate_confidence_score(
            ctx["active_profile"], pet_profile))

    avg_score = round(sum(scores) / len(scores))
    color = confidence_color(avg_score)

    # ── 2. Suggested questions — cache-first, evergreen fallback ────────────
    # Resolve language
    resolved_lang = language
    if resolved_lang == "auto":
        # Try to load user's preferred language
        raw_user = await vk.get(CacheKeys.user(user_code))
        if raw_user:
            user_data = json.loads(raw_user)
            db_lang = user_data.get("preferred_language", "auto")
            resolved_lang = db_lang if db_lang != "auto" else "JA"
        else:
            resolved_lang = "JA"

    pet_ids_list = list(pet_id)
    cache_key = CacheKeys.suggested_questions(
        user_code, pet_ids_list, resolved_lang)

    # Try cache first
    questions_cached = True
    questions_generated_at = ""
    raw_cached_questions = await vk.get(cache_key)

    if raw_cached_questions is not None:
        try:
            cached = json.loads(raw_cached_questions)
            suggested_questions = cached.get("questions", [])
            questions_generated_at = cached.get("generated_at", "")
        except (json.JSONDecodeError, AttributeError):
            suggested_questions = []
    else:
        suggested_questions = []

    # Cache miss → serve evergreen immediately (no LLM call on mount)
    if not suggested_questions:
        questions_cached = False
        suggested_questions = get_evergreen_questions(
            resolved_lang, pet_count=len(pet_id))

    return {
        "status": "ok",
        "confidence_score": avg_score,
        "confidence_color": color,
        "suggested_questions": suggested_questions,
        "questions_cached": questions_cached,
        "questions_generated_at": questions_generated_at,
    }


# ── Backward-compatible alias ───────────────────────────────────────────────

@router.get("/confidence", summary="Confidence bar score (alias for /setup)")
async def get_confidence(
    request: Request,
    pet_id: List[int] = Query(default=[]),
) -> dict[str, Any]:
    """
    Backward-compatible alias — returns confidence score only.

    Mobile clients may still call this. Returns the same confidence fields
    as /setup but without suggested_questions.
    """
    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher
    vk: ValkeyClient = request.app.state.valkey

    if not pet_id:
        raise HTTPException(
            status_code=400, detail="pet_id query parameter is required.")

    scores: list[int] = []

    for pid in pet_id:
        try:
            pet_profile, aalda_facts = await pet_fetcher.fetch_pet_profile(user_code, pid)
        except PetFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        raw_cached = await vk.get(CacheKeys.profile(pid))
        if raw_cached is not None:
            active_raw = json.loads(raw_cached)
        else:
            try:
                async with get_session() as db_session:
                    ap_repo = ActiveProfileRepo(db_session)
                    active_raw = await ap_repo.read_all(pid)
            except Exception as db_exc:
                logger.error(
                    "DB error in /confidence — pet_id=%d: %s", pid, db_exc)
                raise HTTPException(
                    status_code=503, detail="Database unavailable — please retry.")
            if active_raw:
                await vk.setex(CacheKeys.profile(pid), jittered_ttl(TTL_PROFILE), json.dumps(active_raw))

        ctx = build_pet_context(pet_profile, aalda_facts, active_raw)
        scores.append(calculate_confidence_score(
            ctx["active_profile"], pet_profile))

    avg_score = round(sum(scores) / len(scores))
    color = confidence_color(avg_score)

    return {
        "status": "ok",
        "confidence_score": avg_score,
        "confidence_color": color,
    }
