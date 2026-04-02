# app/main.py
#
# FastAPI application entry point.
#
# What lives here:
#   - App creation + CORS config
#   - Lifespan: connect DB, create LLM provider + agents + pet_fetcher, store on app.state
#   - GET /health_v1 — liveness check (infrastructure, stays with the app)
#   - Error handlers — standardised error contract for Flutter
#   - include_router() calls to wire in route modules
#
# What does NOT live here:
#   - Route handlers (app/routes/)
#   - Business logic (agents/ and services/)
#   - LLM credentials (core/config.py + .env)
#   - Pet data (fetched per-request from AALDA API via pet_fetcher.py)
#
# Session state (Phase 2):
#   In-memory dict: thread_id -> list of messages.
#   Stored on app.state.sessions. Reloaded from PostgreSQL on restart.
#   Write-through: every message persisted to thread_messages table.

# ── Standard library ───────────────────────────────────────────────────────────
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ── Third-party ────────────────────────────────────────────────────────────────
import valkey.asyncio as valkey_lib
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── Our code ───────────────────────────────────────────────────────────────────
from app.cache.client import ValkeyClient
from app.cache.keys import CacheKeys, TTL_HEALTH, jittered_ttl
from app.core.config import settings
from app.llm.factory import create_llm_provider
from app.agents.aggregator import AggregatorAgent
from app.agents.compressor import CompressorAgent
from app.agents.conversation import ConversationAgent
from app.agents.intent_classifier import IntentClassifier
from app.agents.suggested_questions import SuggestedQuestionsAgent
from app.services.pet_fetcher import PetFetcher
from sqlalchemy import text
from app.db.session import init_db, dispose_engine, get_session
from app.db.repositories import UserRepo, ThreadRepo, ThreadMessageRepo
from app.services.thread_summarizer import ThreadSummarizer
from app.services.history_builder import HistoryBuilder
from app.services.relationship_builder import RelationshipBuilder
from app.jobs.nightly import run_nightly_jobs

# ── Route modules ─────────────────────────────────────────────────────────────
from app.routes.chat import router as chat_router
from app.routes.debug import router as debug_router
from app.routes.simulator import router as simulator_router


# ── Logging setup ──────────────────────────────────────────────────────────────
# Configured once here. Every other module uses logging.getLogger(__name__)
# and inherits this config automatically.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup (before yield): connect DB, create LLM provider + agents, store on app.state.
    Shutdown (after yield): close DB connection pool.

    Route handlers access these via request.app.state.<name>.
    No module-level globals needed — everything flows through app.state.
    """
    logger.info("Starting up AnyMall-chan backend...")

    # ── Database (Phase 1C) ───────────────────────────────────────────────
    # Fail fast: if DATABASE_URL is not set, crash with a clear error.
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set in .env. "
            "Phase 1C requires PostgreSQL. "
            "Run: docker compose up -d  and set DATABASE_URL in .env"
        )

    await init_db(settings.database_url)

    # ── Valkey cache pool (ft-005) ────────────────────────────────────────
    # Create the connection pool once at startup and store on app.state.
    # decode_responses=True → get str, not bytes (no .decode() needed in code).
    # socket_timeout=5.0    → if Valkey hangs, fall back to DB in 5s max.
    # health_check_interval → auto-PING idle connections before reuse so we
    #                         detect stale TCP connections early.
    raw_vk = valkey_lib.Valkey.from_url(
        settings.valkey_url,
        decode_responses=True,
        max_connections=20,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        health_check_interval=30,
        retry_on_timeout=True,
        socket_keepalive=True,
    )
    app.state.valkey = ValkeyClient(raw_vk)

    # Probe Valkey at startup (non-fatal — app runs without it).
    vk_ok = await app.state.valkey.ping()
    if vk_ok:
        logger.info("Valkey connected at %s", settings.valkey_url)
    else:
        logger.warning(
            "Valkey unreachable at %s — cache disabled, falling back to DB.",
            settings.valkey_url,
        )

    # ── APScheduler — nightly maintenance jobs (Sprint 6) ────────────────
    # AsyncIOScheduler runs jobs inside the existing asyncio event loop —
    # no threads needed. Jobs are registered after all services are init'd below.
    scheduler = AsyncIOScheduler(timezone="UTC")
    app.state.scheduler = scheduler
    scheduler.start()
    logger.info("APScheduler started (UTC timezone)")

    # ── AALDA API client (fetches real pet data per-request) ──────────────
    app.state.pet_fetcher = PetFetcher(
        settings.aalda_api_url,
        timeout=settings.aalda_timeout_seconds,
        valkey=app.state.valkey,
    )

    # ── LLM + Agents ─────────────────────────────────────────────────────
    llm = create_llm_provider(settings)

    app.state.llm_provider = llm
    app.state.agent = ConversationAgent(llm=llm)
    app.state.intent_classifier = IntentClassifier(llm=llm)
    app.state.compressor = CompressorAgent(llm=llm)
    app.state.aggregator = AggregatorAgent(
        get_session=get_session, valkey=app.state.valkey)
    app.state.thread_summarizer = ThreadSummarizer(llm=llm)
    app.state.history_builder = HistoryBuilder(llm=llm)
    app.state.suggested_questions_agent = SuggestedQuestionsAgent(llm=llm)

    # ── RelationshipBuilder — USER STYLE summaries → relationship_summary ─
    # UserProfileWriter Protocol: current impl writes directly to PostgreSQL.
    # Swap _DBUserWriter for an AALDA-backed writer later — zero changes here.
    class _DBUserWriter:
        async def update_relationship_summary(self, user_code: str, summary: str) -> None:
            async with get_session() as db_session:
                repo = UserRepo(db_session)
                await repo.update_relationship_summary(user_code, summary)

    app.state.relationship_builder = RelationshipBuilder(
        llm=llm,
        user_writer=_DBUserWriter(),
    )

    # ── Session state (ft-005: now lives in Valkey) ───────────────────────
    # Sessions are no longer reloaded from DB at startup — they are fetched
    # from Valkey on first access (cache-aside), falling back to DB on miss.
    # This eliminates the cold-start DB query that grew linearly with users.
    #
    # app.state.sessions is kept as an empty dict for any remaining code that
    # reads from it directly — those paths are updated in chat.py to use Valkey.
    app.state.sessions = {}
    app.state.session_meta = {}   # kept for fallback when Valkey is down
    # local fallback when Valkey is down (W3)
    app.state.compaction_in_progress = set()
    # per-thread locks (C2)
    app.state.thread_locks: dict[str, asyncio.Lock] = {}
    # per-pet locks (race condition safety)
    app.state.pet_locks: dict[int, asyncio.Lock] = {}
    # tracked tasks for graceful shutdown (W8)
    app.state.background_tasks: set[asyncio.Task] = set()
    # local fallback when Valkey is down (C4)
    app.state.pending_clarifications: dict[str, list] = {}

    # ── Register nightly cron job — after all services are initialised ────
    # Jobs run at 00:00 UTC. replace_existing=True so restart doesn't duplicate.
    app.state.scheduler.add_job(
        run_nightly_jobs, "cron", hour=0, minute=0,
        args=[app.state], id="nightly_jobs", replace_existing=True,
    )
    logger.info("Nightly job registered — runs at 00:00 UTC")

    logger.info("Backend ready. LLM provider: %s", settings.llm_provider)

    yield

    # ── Shutdown (W8 — graceful task tracking) ────────────────────────────
    # Wait for in-flight background tasks (Compressor/Aggregator/Compaction)
    # to finish their DB writes before the connection pool is disposed.
    pending = app.state.background_tasks
    if pending:
        logger.info(
            "Shutting down — waiting for %d background task(s)...", len(pending))
        done, timed_out = await asyncio.wait(pending, timeout=10)
        if timed_out:
            logger.warning(
                "Shutdown: %d task(s) did not finish in 10s — cancelling.",
                len(timed_out),
            )
            for task in timed_out:
                task.cancel()
            # Give cancelled tasks a moment to handle CancelledError
            await asyncio.wait(timed_out, timeout=2)
    else:
        logger.info("Shutting down — no background tasks pending.")

    # Shutdown scheduler — wait=True (default) so any in-flight nightly job finishes
    # its current DB writes before we dispose the connection pool below.
    # APScheduler raises on timeout if wait=True and jobs are still running after the
    # internal grace period, but it won't block indefinitely.
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown(wait=True)
        logger.info("APScheduler shut down.")

    await app.state.pet_fetcher.close()
    await app.state.valkey.aclose()
    await dispose_engine()
    logger.info("Shutdown complete.")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AnyMall-chan API",
    description="Pet companion chat backend — API v1",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow all origins during development.
# Phase 4: change allow_origins to specific Flutter/web domains.
# NOTE: allow_credentials must be False when allow_origins=["*"].
#       The CORS spec forbids credentials=True with a wildcard origin —
#       browsers will reject the response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # must be False with wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Error handlers — standardised error contract ─────────────────────────────
#
# Every error response follows the same shape:
#   {"status": "error", "error": {"code": "...", "message": "..."}}
#
# Flutter checks `status` field first, then reads `error.code` + `error.message`.

_ERROR_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: StarletteHTTPException) -> JSONResponse:
    """Wrap all HTTP errors into the standard error shape."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": {
                "code": _ERROR_CODES.get(exc.status_code, "UNKNOWN_ERROR"),
                "message": str(exc.detail),
            },
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
    """Wrap Pydantic validation errors into the standard error shape."""
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "error": {
                "code": "VALIDATION_ERROR",
                "message": str(exc),
            },
        },
    )


# ── Register route modules ────────────────────────────────────────────────────

app.include_router(chat_router)
app.include_router(debug_router)
app.include_router(simulator_router)


# ── Infrastructure route (stays in main.py — no /api/v1 prefix) ──────────────

@app.get("/health", summary="Deep health check")
async def health_v1() -> JSONResponse:
    """
    Returns 200 if all critical dependencies are healthy, 503 if DB is down.

    Checks three things:
      1. DB  — SELECT 1 (not cached: must reflect real-time DB state)
      2. Valkey — ping (not cached: fast, real-time)
      3. LLM — health_check() cached in Valkey for 60s (S-09 fix, expensive)

    Status rules:
      - db_ok=False → 503 (DB is critical; every request needs it)
      - valkey_ok=False → 200 with status "degraded" (app still runs, just slower)
      - llm_ok=False → 200 with status "degraded" (users get errors but app is up)
      - all ok → 200 with status "ok"

    Why no cache on DB/Valkey checks?
      SELECT 1 takes ~1ms and uses a pool connection — near-zero cost.
      Valkey ping is a single TCP round-trip.  Caching these would defeat the
      purpose: a monitoring tool must see a DB outage within one poll cycle.
    """
    vk: ValkeyClient = getattr(app.state, "valkey", None)

    # ── 1. DB check (SELECT 1) ────────────────────────────────────────────
    db_ok = False
    try:
        async with get_session() as db_session:
            await db_session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as db_exc:
        logger.error("Health check: DB unreachable — %s", db_exc)

    # ── 2. Valkey check ───────────────────────────────────────────────────
    valkey_ok = False
    if vk is not None:
        valkey_ok = await vk.ping()

    # ── 3. LLM check (cached 60s to avoid burning API quota) ─────────────
    llm_ok = False
    if vk is not None and valkey_ok:
        cached_raw = await vk.get(CacheKeys.health_llm())
        if cached_raw is not None:
            llm_ok = json.loads(cached_raw).get("llm_reachable", False)

    if not llm_ok:
        llm_provider = getattr(app.state, "llm_provider", None)
        if llm_provider is not None:
            llm_ok = await llm_provider.health_check()
        if vk is not None and valkey_ok:
            await vk.setex(
                CacheKeys.health_llm(),
                jittered_ttl(TTL_HEALTH),
                json.dumps({"llm_reachable": llm_ok}),
            )

    # ── Determine overall status and HTTP code ────────────────────────────
    if not db_ok:
        status = "unavailable"
    elif not valkey_ok or not llm_ok:
        status = "degraded"
    else:
        status = "ok"

    http_code = 503 if not db_ok else 200

    result: dict[str, Any] = {
        "status": status,
        "db": db_ok,
        "valkey": valkey_ok,
        "llm_provider": settings.llm_provider,
        "llm_reachable": llm_ok,
        "version": "1.2.0",
    }

    return JSONResponse(status_code=http_code, content=result)


# ── Serve React frontend build (production only) ────────────────────────────
#
# When frontend/dist/ exists (after `npm run build`), FastAPI serves the React
# app from the same URL. No separate frontend server needed in production.
#
# During development (npm run dev + uvicorn), frontend/dist/ doesn't exist,
# so none of this activates — dev workflow is unchanged.

# Check two locations:
#   1. ../frontend/dist/  — local dev (after npm run build)
#   2. ./frontend_dist/   — Render deploy (build command copies dist here)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND_DIST = _BACKEND_ROOT.parent / "frontend" / "dist"      # local
if not _FRONTEND_DIST.is_dir():
    _FRONTEND_DIST = _BACKEND_ROOT / "frontend_dist"               # Render

if _FRONTEND_DIST.is_dir():
    logger.info(
        "Frontend build found at %s — serving static files.", _FRONTEND_DIST)

    # Serve JS/CSS bundles from dist/assets/
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )

    # Catch-all: any path not matched by API routes → serve index.html
    # React handles client-side routing from there.
    # MUST be last — after all API routes and include_router() calls.
    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str) -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")
