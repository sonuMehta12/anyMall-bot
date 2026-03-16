# app/main.py
#
# FastAPI application entry point.
#
# What lives here:
#   - App creation + CORS config
#   - Lifespan: connect DB, create LLM provider + agents + pet_fetcher, store on app.state
#   - GET /health — liveness check (infrastructure, stays with the app)
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
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── Our code ───────────────────────────────────────────────────────────────────
from app.core.config import settings
from app.llm.factory import create_llm_provider
from app.agents.conversation import ConversationAgent
from app.agents.intent_classifier import IntentClassifier
from app.agents.compressor import CompressorAgent
from app.agents.aggregator import AggregatorAgent
from app.services.pet_fetcher import PetFetcher
from app.db.session import init_db, dispose_engine, get_session
from app.db.repositories import PetRepo, ThreadRepo, ThreadMessageRepo
from app.services.thread_summarizer import ThreadSummarizer

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

    # ── AALDA API client (fetches real pet data per-request) ──────────────
    # DB callbacks for fallback (W1) and persistence (W10)
    async def _pet_db_fallback(pet_id: int) -> dict | None:
        async with get_session() as session:
            return await PetRepo(session).read(pet_id)

    async def _pet_db_persist(pet_profile: dict) -> None:
        async with get_session() as session:
            await PetRepo(session).upsert(pet_profile)

    app.state.pet_fetcher = PetFetcher(
        settings.aalda_api_url,
        db_fallback=_pet_db_fallback,
        db_persist=_pet_db_persist,
        timeout=settings.aalda_timeout_seconds,
    )

    # ── LLM + Agents ─────────────────────────────────────────────────────
    llm = create_llm_provider(settings)

    app.state.llm_provider = llm
    app.state.agent = ConversationAgent(llm=llm)
    app.state.intent_classifier = IntentClassifier(llm=llm)
    app.state.compressor = CompressorAgent(llm=llm)
    app.state.aggregator = AggregatorAgent(get_session=get_session)
    app.state.thread_summarizer = ThreadSummarizer(llm=llm)

    # ── Phase 2: Reload active threads from PostgreSQL ─────────────────
    # Read from DB at startup,
    # populate app.state so runtime reads are in-memory only.
    async with get_session() as session:
        thread_repo = ThreadRepo(session)
        msg_repo = ThreadMessageRepo(session)
        active_threads = await thread_repo.get_all_active()
        sessions: dict[str, list] = {}
        for thread in active_threads:
            # Only load messages after the compaction cutoff (W12)
            after_id = thread.get("compacted_before_id")
            messages = await msg_repo.read_thread(thread["thread_id"], after_id=after_id)
            sessions[thread["thread_id"]] = messages
        app.state.sessions = sessions
        logger.info("Loaded %d active thread(s) from database.", len(sessions))

    app.state.session_meta = {}   # thread_id -> tracking metadata (gap questions, cooldowns)
    app.state.compaction_in_progress = set()  # thread_ids currently being compacted (W3)
    app.state.thread_locks: dict[str, asyncio.Lock] = {}  # per-thread locks (C2 — concurrent session safety)
    app.state.background_tasks: set[asyncio.Task] = set()  # tracked tasks for graceful shutdown (W8)

    logger.info("Backend ready. LLM provider: %s", settings.llm_provider)

    yield

    # ── Shutdown (W8 — graceful task tracking) ────────────────────────────
    # Wait for in-flight background tasks (Compressor/Aggregator/Compaction)
    # to finish their DB writes before the connection pool is disposed.
    pending = app.state.background_tasks
    if pending:
        logger.info("Shutting down — waiting for %d background task(s)...", len(pending))
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

    await app.state.pet_fetcher.close()
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

@app.get("/health", summary="Liveness check")
async def health() -> dict[str, Any]:
    """Returns 200 if server is up. Checks LLM reachability via health_check()."""
    llm_ok = False
    llm_provider = getattr(app.state, "llm_provider", None)
    if llm_provider is not None:
        llm_ok = await llm_provider.health_check()

    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_reachable": llm_ok,
        "version": "1.0.0",
    }


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
    logger.info("Frontend build found at %s — serving static files.", _FRONTEND_DIST)

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
