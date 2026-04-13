# app/routes/chat.py
#
# POST /api/v1/chat — the core endpoint.
# GET  /api/v1/pets — list user's pets from AALDA.
# POST /api/v1/pets/setup/query — confidence bar + suggested questions.
# GET  /api/v1/confidence — backward-compat alias (confidence score only).
#
# What lives here:
#   - Pydantic request/response models (ChatRequest, ChatResponse, RedirectPayload, SetupRequest)
#   - POST /api/v1/chat route
#   - GET /api/v1/pets route (fetches from AALDA)
#   - POST /api/v1/pets/setup/query route (confidence + suggested questions)
#   - GET /api/v1/confidence route (alias, backward compat)
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
from pydantic import BaseModel, Field, field_validator

# ── Our code ───────────────────────────────────────────────────────────────────
from app.routes.background import _create_tracked_task, _run_background
from app.agents.conversation import AgentResponse
from app.agents.state import AgentState, PetInfo
from app.services.guardrails import apply_guardrails, detect_prompt_injection
from app.services.deeplink import build_deeplink
from app.services.context_builder import build_pet_context
from app.services.pet_fetcher import PetFetchError
from app.services.recipe_fetcher import RecipeFetcher, RecipeFetchError
from app.services.food_query_planner import plan_food_queries
from app.agents.food_agent import FoodAgent, FoodAgentResult
from app.cache.client import ValkeyClient
from app.cache.keys import (
    CacheKeys, TTL_SESSION, TTL_META, TTL_PENDING, TTL_USER, TTL_PROFILE,
    TTL_SUGGESTED, TTL_SUGGESTED_HISTORY, jittered_ttl,
)
from constants import (
    INTENT_HEALTH, INTENT_FOOD,
    INTENT_FOOD_RECIPES, INTENT_FOOD_RECIPES_INFO, INTENT_FOOD_INFO,
    INTENT_UNTRUSTED, URGENCY_HIGH, URGENCY_MEDIUM,
    THREAD_CONTEXT_WINDOW, THREAD_EXPIRY_HOURS,
)
from app.db.session import get_session
from app.db.repositories import ActiveProfileRepo, SuggestedQuestionsRepo, ThreadRepo, ThreadMessageRepo, UserRepo
from app.services.confidence_calculator import calculate_confidence_score, confidence_color
from app.services.question_generation.templates import get_evergreen_questions
from app.services.question_generation.generator import _build_full_evergreen

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

    @field_validator("message")
    @classmethod
    def message_not_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message must contain non-whitespace characters.")
        return stripped

    @field_validator("language")
    @classmethod
    def normalize_language(cls, v: str) -> str:
        # Strip and uppercase so "en", " JA ", "auto" all normalise correctly.
        normalized = v.strip().upper()
        return normalized if normalized in ("EN", "JA") else "auto"


class RedirectDisplay(BaseModel):
    """How the client should render the redirect button."""
    label: str    # "Talk to Health Assistant" | "Talk to Food Specialist"
    style: str    # "urgent" (red) | "suggestion" (orange)


class RedirectContext(BaseModel):
    """Data the target module needs to function."""
    query: str        # user's original message, pre-filled in the module
    pet_id: int       # which pet, so the module can fetch its own data
    pet_summary: str  # full NL pet context — by design, module needs this
    recipes_by_pet: dict[str, list[dict]] = Field(default_factory=dict)


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
    output_mode: str = "general"  # "food_recipes"|"food_recipes_info"|"food_info"|"general"
    # present only for health/food intents
    redirect: RedirectPayload | None = None
    recipes_by_pet: dict[str, list[dict]] = Field(default_factory=dict)  # Phase 3: recipes for food intent
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


class SetupRequest(BaseModel):
    """Body for POST /api/v1/pets/setup/query."""
    pet_ids: List[int] = Field(..., min_length=1, description="One or more pet IDs")
    language: str = Field(default="auto")
    module: str = Field(default="anymall", pattern="^(anymall|food|health)$")


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
            recipes_by_pet={str(k): v for k, v in deeplink.recipes_by_pet.items()}
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
      4. IntentClassifier — LLM: intent_type + urgency (health/food/general/untrusted).
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

    # ── Layer 1a: Pattern-based injection check (T1-04) ───────────────────────
    # Runs before AALDA fetch, before any LLM call. Zero cost.
    # Catches obvious attacks immediately — no pipeline created, no DB touched.
    if detect_prompt_injection(request_body.message):
        raise HTTPException(
            status_code=400, detail="I can't help with that.")

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

    # Normalize: replace any None entries with {} so downstream code never
    # crashes on .get() — happens when a pet has no active profile in DB yet.
    active_profiles_raw = [p if p is not None else {} for p in active_profiles_raw]

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
                    "all_pet_ids": pet_ids,
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
                # Merge pet IDs: preserve canonical order, never shrink the list.
                # If a user chats with only pet B, we must NOT overwrite [101, 102]
                # with [102] — that would make pet 102 regenerate as pet_a next nightly.
                current_known: list[int] = user_record.get("last_known_pet_ids") or []
                merged_pet_ids: list[int] = list(
                    dict.fromkeys(current_known + pet_ids)  # order-preserving dedup
                )
                updated = {
                    "user_code": user_code,
                    "display_name": new_display,
                    "updated_at": now_iso,
                    # Increment only when a new 24-hour thread window opens.
                    "session_count": user_record.get("session_count", 0) + (1 if new_thread else 0),
                    "relationship_summary": user_record.get("relationship_summary", ""),
                    "preferred_language": new_lang,
                    "all_pet_ids": merged_pet_ids,
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
        intent_type, urgency = await intent_classifier.classify(
            request_body.message,
            recent_history=list(session_messages[-4:]),
        )

        # ── Layer 1b: LLM-based injection check (T1-04) ──────────────────────
        # Catches subtle attacks that patterns miss ("pretend to be a different AI",
        # indirect jailbreaks, etc.). No background task is created for untrusted
        # messages — the pipeline exits here, so no DB poisoning is possible.
        if intent_type == INTENT_UNTRUSTED:
            logger.warning(
                "Untrusted intent detected — rejecting. user=%s snippet=%r",
                user_code, request_body.message[:80],
            )
            raise HTTPException(
                status_code=400, detail="I can't help with that.")

        # ── Language priority (hoisted — needed by FoodAgent too) ────────────
        if request_body.language != "auto":
            language_str = request_body.language
        else:
            db_lang = (user_record or {}).get("preferred_language", "auto")
            language_str = db_lang if db_lang != "auto" else _detect_language(
                request_body.message)

        # ── 5b. Food AI routing ───────────────────────────────────────────────
        # food_recipes  → MCP only, return cards, message="", no LLM
        # food_recipes_info → FoodAgent (handles MCP + Tavily + LLM internally)
        # food_info     → FoodAgent (Tavily + LLM only, no recipe cards)
        # health/general → ConversationAgent (below, unchanged)
        _FOOD_INTENTS = {INTENT_FOOD_RECIPES, INTENT_FOOD_RECIPES_INFO, INTENT_FOOD_INFO, INTENT_FOOD}

        if intent_type in _FOOD_INTENTS:
            # Normalize legacy "food" intent (old classifier behavior / backward compat)
            # → treat as food_info so it reaches FoodAgent rather than silently falling
            # through to ConversationAgent.
            if intent_type == INTENT_FOOD:
                logger.warning("Received legacy intent 'food' — normalising to food_info")
                intent_type = INTENT_FOOD_INFO
            output_mode: str = intent_type

            # Species override: if ALL pets are non-dog → force food_info
            if all(p.get("species") != "dog" for p in pet_profiles):
                intent_type = INTENT_FOOD_INFO
                output_mode = INTENT_FOOD_INFO

            food_agent: FoodAgent = state_bag.food_agent
            recipe_fetcher_inst: RecipeFetcher = state_bag.recipe_fetcher

            if intent_type == INTENT_FOOD_RECIPES:
                # ── Mode 1: MCP only, no LLM ────────────────────────────────
                # Build the MCP query via the planner (nano LLM) so Mode 1 gets
                # the same query quality as Modes 2 and 3.
                _m1_plan = await plan_food_queries(
                    llm=state_bag.llm_provider,
                    mode=INTENT_FOOD_RECIPES,
                    user_message=request_body.message,
                    pet_profile=pet_profiles[0] if pet_profiles else {},
                    active_profile=active_profiles_raw[0] if active_profiles_raw else {},
                    conversation_history=list(session_messages[-6:]),
                    language=language_str,
                )
                recipes_by_pet_mode1: dict[int, list[dict]] = {}
                try:
                    raw_m1 = await recipe_fetcher_inst.fetch_for_pets(
                        pre_built_query=_m1_plan.mcp_query,
                        pet_profiles=pet_profiles,
                        active_profiles=active_profiles_raw,
                        conversation_history=list(session_messages[-6:]),
                    )
                    for pid, (rs, _fb) in raw_m1.items():
                        recipes_by_pet_mode1[pid] = [dict(r) for r in rs]
                    logger.info(
                        "Food Mode 1 (food_recipes): %s",
                        {pid: len(v) for pid, v in recipes_by_pet_mode1.items()},
                    )
                except RecipeFetchError as exc:
                    logger.warning("Food Mode 1: MCP unavailable — returning empty: %s", exc)
                    # MCP is down — return a text fallback so the user gets a response
                    # instead of a blank turn (empty message + empty cards).
                    fallback_text = (
                        "レシピを取得できませんでした。しばらくしてからもう一度お試しください。"
                        if language_str == "JA"
                        else "I couldn't load recipes right now. Please try again in a moment."
                    )
                    user_msg_err = {"role": "user", "content": request_body.message, "timestamp": now_iso}
                    asst_msg_err = {"role": "assistant", "content": fallback_text, "timestamp": now_iso}
                    sessions[thread_id].append(user_msg_err)
                    sessions[thread_id].append(asst_msg_err)
                    agent_state.agent_reply = fallback_text
                    agent_state.recent_history = list(sessions[thread_id])
                    _create_tracked_task(_run_background(agent_state, state_bag), state_bag)
                    return ChatResponse(
                        message=fallback_text,
                        output_mode=INTENT_FOOD_RECIPES,
                        recipes_by_pet={},
                        redirect=None,
                        session_id=session_id,
                        thread_id=thread_id,
                        new_thread=new_thread,
                        questions_asked_count=questions_so_far,
                        was_guardrailed=False,
                        is_entity=False,
                        intent_type=intent_type,
                        urgency=urgency,
                        confidence_score=conf_score,
                        confidence_color=conf_color,
                    )

                # Build a synthetic assistant message so ConversationAgent knows what
                # recipe cards were shown on the next turn (follow-up handling).
                # Format: "[Recipe cards shown for <Pet>: <title1>, <title2>, ...]"
                recipe_card_lines = []
                for pid, recipes_for_pet in recipes_by_pet_mode1.items():
                    pet_match = next((p for p in pet_profiles if p.get("pet_id") == int(pid)), None)
                    pet_label = pet_match.get("name", f"pet {pid}") if pet_match else f"pet {pid}"
                    titles = ", ".join(r.get("title_ja", "Unknown") for r in recipes_for_pet)
                    if titles:
                        recipe_card_lines.append(f"{pet_label}: {titles}")
                if recipe_card_lines:
                    recipe_card_summary = "[Recipe cards shown — " + "; ".join(recipe_card_lines) + "]"
                else:
                    recipe_card_summary = "[Recipe cards requested but no results found]"

                user_msg_m1 = {"role": "user", "content": request_body.message, "timestamp": now_iso}
                asst_msg_m1 = {"role": "assistant", "content": recipe_card_summary, "timestamp": now_iso}
                sessions[thread_id].append(user_msg_m1)
                sessions[thread_id].append(asst_msg_m1)

                # Fire-and-forget: persist to Valkey + DB so the session survives restart
                agent_state.agent_reply = recipe_card_summary
                agent_state.recent_history = list(sessions[thread_id])
                _create_tracked_task(_run_background(agent_state, state_bag), state_bag)

                recipes_str_m1 = {str(k): v for k, v in recipes_by_pet_mode1.items()}
                return ChatResponse(
                    message="",
                    output_mode=INTENT_FOOD_RECIPES,
                    recipes_by_pet=recipes_str_m1,
                    redirect=None,
                    session_id=session_id,
                    thread_id=thread_id,
                    new_thread=new_thread,
                    questions_asked_count=questions_so_far,
                    was_guardrailed=False,
                    is_entity=False,
                    intent_type=intent_type,
                    urgency=urgency,
                    confidence_score=conf_score,
                    confidence_color=conf_color,
                )

            # ── Modes 2 and 3: FoodAgent ────────────────────────────────────
            food_result: FoodAgentResult = await food_agent.run(
                mode=intent_type,
                user_message=request_body.message,
                recipe_fetcher=recipe_fetcher_inst,
                pet_profiles=pet_profiles,
                active_profiles=active_profiles_raw,
                session_messages=list(session_messages[-20:]),
                language=language_str,
            )

            food_reply_raw = food_result.message
            food_recipes_by_pet = food_result.recipes_by_pet  # {pet_id: [recipe_dict, ...]}
            # Use the mode the agent actually ran — may be downgraded from food_recipes_info
            # to food_info when MCP returned 0 recipes. This keeps output_mode truthful.
            if food_result.effective_mode:
                output_mode = food_result.effective_mode

            # Apply guardrails
            guardrail_food = apply_guardrails(food_reply_raw)
            final_food_reply = guardrail_food.reply
            food_guardrailed = guardrail_food.was_modified

            # Save to session synchronously
            user_msg_food = {"role": "user", "content": request_body.message, "timestamp": now_iso}
            asst_msg_food = {"role": "assistant", "content": final_food_reply, "timestamp": now_iso}
            sessions[thread_id].append(user_msg_food)
            sessions[thread_id].append(asst_msg_food)

            # Fire-and-forget: Valkey + DB persistence (same pattern as ConversationAgent)
            agent_state.agent_reply = final_food_reply
            agent_state.recent_history = list(sessions[thread_id])
            _create_tracked_task(_run_background(agent_state, state_bag), state_bag)

            recipes_str_food = {str(k): v for k, v in food_recipes_by_pet.items()}
            logger.info(
                "Food complete — mode=%s | session=%s | recipe_count=%d | guardrailed=%s",
                intent_type, session_id, sum(len(v) for v in food_recipes_by_pet.values()),
                food_guardrailed,
            )
            return ChatResponse(
                message=final_food_reply,
                output_mode=output_mode,
                recipes_by_pet=recipes_str_food,
                redirect=None,
                session_id=session_id,
                thread_id=thread_id,
                new_thread=new_thread,
                questions_asked_count=questions_so_far,
                was_guardrailed=food_guardrailed,
                is_entity=False,
                asked_gap_question=False,
                intent_type=intent_type,
                urgency=urgency,
                confidence_score=conf_score,
                confidence_color=conf_color,
            )

        # ── 6. Agent 1 (health / general) ───────────────────────────────────
        pet_a_context = pet_contexts[0]
        pet_b_context = pet_contexts[1] if len(pet_contexts) > 1 else None

        # Load pending clarifications — Valkey GET (ft-005, Step 5)
        raw_pending = await vk.get(CacheKeys.pending(thread_id))
        if raw_pending is not None:
            pending_clars = json.loads(raw_pending)
        else:
            pending_clars = state_bag.pending_clarifications.get(thread_id, [])

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

        # ── 7b. Build deeplink (health intent only) ──────────────────────────
        # Food intent no longer uses redirect — recipes are handled via agent context
        MEDIUM_COOLDOWN = 3

        redirect_payload = None
        pet_summary_primary = primary_ctx["pet_summary"]
        if intent_type == INTENT_HEALTH:
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
        output_mode="general",
        redirect=redirect_payload,
        recipes_by_pet={},  # health/general intents never carry recipe cards
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


# ── Suggested questions pick rule ────────────────────────────────────────────

def _pick_questions(
    rows: dict[int, list[dict]],
    pet_ids: list[int],
    module: str,
    language: str,
) -> list[dict]:
    """
    Select 3 questions from per-pet question rows for a given module.

    Each pet has its own 10-question row (keyed by pet_id). The pick rule reads
    from those rows and falls back to evergreen for any missing slot.

    Pick rule:
      food   / single pet → primary row food/this_pet[0,1,2]    (3 dedicated)
      food   / dual pet   → primary food/pet_a[0] + secondary food/pet_b[0] + primary food/both[0]
      health / single pet → primary row health/this_pet[0,1,2]
      health / dual pet   → primary health/pet_a[0] + secondary health/pet_b[0] + primary health/both[0]
      anymall / single    → primary food/tgt[0] + primary health/tgt[0] + primary anymall/tgt[0]
      anymall / dual      → primary food/pet_a[0] + secondary health/pet_b[0] + primary anymall/both[0]

    Always returns exactly 3 items.
    """
    primary = rows.get(pet_ids[0], []) if pet_ids else []
    secondary = rows.get(pet_ids[1], []) if len(pet_ids) >= 2 else []

    # Detect the target label each row actually uses for its dedicated questions.
    # A pet_a row has target="pet_a" on slots 0-2; a pet_b row uses target="pet_b".
    # We infer rather than hardcode so that selecting pet B alone works correctly.
    _primary_dedicated = [q for q in primary if q.get("target") in ("pet_a", "pet_b")]
    primary_tgt = _primary_dedicated[0]["target"] if _primary_dedicated else "pet_a"
    _secondary_dedicated = [q for q in secondary if q.get("target") in ("pet_a", "pet_b")]
    secondary_tgt = _secondary_dedicated[0]["target"] if _secondary_dedicated else "pet_b"

    def pick_from(questions: list[dict], mod: str, tgt: str, n: int = 1) -> list[dict]:
        """Take up to n from questions matching mod+tgt, fill remainder with evergreen."""
        matches = [q for q in questions if q.get("module") == mod and q.get("target") == tgt]
        result = matches[:n]
        while len(result) < n:
            ev = get_evergreen_questions(mod, language, tgt, count=1)
            result.append(ev[0] if ev else {"text": "", "module": mod, "target": tgt})
        return result

    if module == "food":
        if secondary:
            return (pick_from(primary, "food", primary_tgt) +
                    pick_from(secondary, "food", secondary_tgt) +
                    pick_from(primary, "food", "both"))
        else:
            return pick_from(primary, "food", primary_tgt, 3)

    elif module == "health":
        if secondary:
            return (pick_from(primary, "health", primary_tgt) +
                    pick_from(secondary, "health", secondary_tgt) +
                    pick_from(primary, "health", "both"))
        else:
            return pick_from(primary, "health", primary_tgt, 3)

    else:  # anymall
        if secondary:
            return (pick_from(primary, "food", primary_tgt) +
                    pick_from(secondary, "health", secondary_tgt) +
                    pick_from(primary, "anymall", "both"))
        else:
            return (pick_from(primary, "food", primary_tgt) +
                    pick_from(primary, "health", primary_tgt) +
                    pick_from(primary, "anymall", primary_tgt))


# ── Setup endpoint (confidence + suggested questions) ────────────────────────

@router.post("/pets/setup/query", summary="Confidence bar + suggested questions")
async def get_setup(
    request: Request,
    body: SetupRequest,
) -> dict[str, Any]:
    """
    Returns confidence score + suggested home screen questions.

    Accepts 1–N pet IDs in the JSON body.
    Single pet:  {"pet_ids": [101], "module": "food"}
    Dual pet:    {"pet_ids": [101, 102], "module": "food"}

    Requires X-User-Code header. Called by the frontend on mount.
    """
    pet_id = body.pet_ids
    language = body.language
    module = body.module

    user_code = _require_user_code(request)
    pet_fetcher = request.app.state.pet_fetcher
    vk: ValkeyClient = request.app.state.valkey

    if not pet_id:
        raise HTTPException(
            status_code=400, detail="pet_ids must contain at least one pet ID.")

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

    # ── 2. Suggested questions — Valkey → Postgres → evergreen ──────────────
    # Resolve language
    resolved_lang = language
    if resolved_lang == "auto":
        raw_user = await vk.get(CacheKeys.user(user_code))
        if raw_user:
            user_data = json.loads(raw_user)
            db_lang = user_data.get("preferred_language", "auto")
            resolved_lang = db_lang if db_lang != "auto" else "JA"
        else:
            # Valkey cold — fall back to DB before defaulting to JA
            try:
                async with get_session() as db_session:
                    user_repo = UserRepo(db_session)
                    user_record = await user_repo.read(user_code)
                if user_record:
                    db_lang = user_record.get("preferred_language", "auto")
                    resolved_lang = db_lang if db_lang != "auto" else "JA"
                else:
                    resolved_lang = "JA"
            except Exception:
                resolved_lang = "JA"

    questions_cached = True
    questions_generated_at = ""
    # Per-pet rows: pet_id → list of 10 questions for that pet
    per_pet_rows: dict[int, list[dict]] = {}

    # Step 1: Valkey hot cache — load each pet's row separately
    for pid in pet_id:
        cache_key = CacheKeys.suggested_questions(user_code, resolved_lang, pid)
        raw = await vk.get(cache_key)
        if raw is not None:
            try:
                cached = json.loads(raw)
                per_pet_rows[pid] = cached.get("questions", [])
                if not questions_generated_at:
                    questions_generated_at = cached.get("generated_at", "")
            except (json.JSONDecodeError, AttributeError):
                pass

    # Step 2: Valkey miss per pet → try Postgres cold storage
    missing_pids = [pid for pid in pet_id if pid not in per_pet_rows]
    if missing_pids:
        try:
            async with get_session() as db_session:
                sq_repo = SuggestedQuestionsRepo(db_session)
                for pid in missing_pids:
                    pg_row = await sq_repo.get(user_code, resolved_lang, pid)
                    if pg_row:
                        per_pet_rows[pid] = pg_row["questions"]
                        if not questions_generated_at and pg_row["generated_at"]:
                            questions_generated_at = pg_row["generated_at"].isoformat()
                        # Warm Valkey from Postgres (best-effort)
                        try:
                            await vk.setex(
                                CacheKeys.suggested_questions(user_code, resolved_lang, pid),
                                jittered_ttl(TTL_SUGGESTED),
                                json.dumps({
                                    "generated_at": pg_row["generated_at"].isoformat() if pg_row.get("generated_at") else "",
                                    "questions": pg_row["questions"],
                                }),
                            )
                        except Exception:
                            pass
        except Exception as db_exc:
            logger.warning("/setup: Postgres fallback failed: %s", db_exc)

    # Step 3: Cold start per pet — neither Valkey nor Postgres has this pet → evergreen
    for i, pid in enumerate(pet_id):
        if pid not in per_pet_rows:
            questions_cached = False
            per_pet_rows[pid] = _build_full_evergreen(resolved_lang, is_pet_b=(i == 1))

    # Apply module pick rule → return 3 questions
    suggested_questions = _pick_questions(per_pet_rows, list(pet_id), module, resolved_lang)

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
