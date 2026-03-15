# app/main.py
#
# FastAPI application entry point.
#
# What lives here:
#   - App creation + CORS config
#   - Lifespan: connect DB, create LLM provider + agents, store on app.state
#   - GET /health — liveness check (infrastructure, stays with the app)
#   - GET /confidence — confidence bar score
#   - include_router() calls to wire in route modules
#
# What does NOT live here:
#   - Route handlers (app/routes/)
#   - Business logic (agents/ and services/)
#   - LLM credentials (core/config.py + .env)
#   - Pet data (context_builder.py reads from PostgreSQL at startup)
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Our code ───────────────────────────────────────────────────────────────────
from app.core.config import settings
from app.llm.factory import create_llm_provider
from app.agents.conversation import ConversationAgent
from app.agents.intent_classifier import IntentClassifier
from app.agents.compressor import CompressorAgent
from app.agents.aggregator import AggregatorAgent
from app.services.context_builder import build_context, load_profiles_from_db
from app.services.confidence_calculator import calculate_confidence_score, confidence_color
from app.db.session import init_db, dispose_engine, get_session
from app.db.repositories import PetRepo, UserRepo, ActiveProfileRepo, ThreadRepo, ThreadMessageRepo
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

    # Load profiles from PostgreSQL (seeds Luna + Shara defaults if empty).
    async with get_session() as session:
        profiles = await load_profiles_from_db(
            pet_repo=PetRepo(session),
            user_repo=UserRepo(session),
            active_repo=ActiveProfileRepo(session),
        )

    app.state.active_profile = profiles["active"]
    app.state.pet_profile = profiles["pet"]
    app.state.user_profile = profiles["user"]

    # ── LLM + Agents ─────────────────────────────────────────────────────
    llm = create_llm_provider(settings)

    app.state.llm_provider = llm
    app.state.agent = ConversationAgent(llm=llm)
    app.state.intent_classifier = IntentClassifier(llm=llm)
    app.state.compressor = CompressorAgent(llm=llm)
    app.state.aggregator = AggregatorAgent(get_session=get_session)
    app.state.thread_summarizer = ThreadSummarizer(llm=llm)

    # ── Phase 2: Reload active threads from PostgreSQL ─────────────────
    # Same pattern as load_profiles_from_db — read from DB at startup,
    # populate app.state so runtime reads are in-memory only.
    async with get_session() as session:
        thread_repo = ThreadRepo(session)
        msg_repo = ThreadMessageRepo(session)
        active_threads = await thread_repo.get_all_active()
        sessions: dict[str, list] = {}
        for thread in active_threads:
            messages = await msg_repo.read_thread(thread["thread_id"])
            sessions[thread["thread_id"]] = messages
        app.state.sessions = sessions
        logger.info("Loaded %d active thread(s) from database.", len(sessions))

    app.state.session_meta = {}   # thread_id -> tracking metadata (gap questions, cooldowns)

    logger.info("Backend ready. LLM provider: %s", settings.llm_provider)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    # Brief grace period so in-flight background tasks (Compressor/Aggregator)
    # can finish their DB writes before the connection pool is disposed.
    logger.info("Shutting down — waiting for background tasks...")
    await asyncio.sleep(2)
    await dispose_engine()
    logger.info("Shutdown complete.")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AnyMall-chan API",
    description="Pet companion chat backend — Phase 2 (Thread Management)",
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


# ── Register route modules ────────────────────────────────────────────────────

app.include_router(chat_router)
app.include_router(debug_router)
app.include_router(simulator_router)


# ── Infrastructure route (stays in main.py) ────────────────────────────────────

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
        "phase": "1C",
    }


@app.get("/confidence", summary="Current confidence bar score")
async def get_confidence() -> dict[str, Any]:
    """
    Returns the current confidence score and color based on active_profile.

    Called by the frontend on mount (before any chat messages) and after
    each chat response with a short delay (to pick up Aggregator writes).

    No LLM — pure arithmetic on in-memory profile data. Sub-millisecond.
    """
    active_profile, _gap_list, _pet_summary, _pet_history, _rel, _conv = build_context(
        active_profile_raw=app.state.active_profile,
        pet_profile=app.state.pet_profile,
        user_profile=app.state.user_profile,
    )
    score = calculate_confidence_score(active_profile, app.state.pet_profile)
    color = confidence_color(score)
    return {
        "confidence_score": score,
        "confidence_color": color,
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
