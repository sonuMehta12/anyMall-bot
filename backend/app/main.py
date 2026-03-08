# app/main.py
#
# FastAPI application entry point.
#
# What lives here:
#   - App creation + CORS config
#   - Lifespan: create agents + LLM provider once, store on app.state
#   - GET /health — liveness check (infrastructure, stays with the app)
#   - include_router() calls to wire in route modules
#
# What does NOT live here:
#   - Route handlers (app/routes/)
#   - Business logic (agents/ and services/)
#   - LLM credentials (core/config.py + .env)
#   - Pet data (context_builder.py reads from data/*.json)
#
# Session state (Phase 0):
#   Simple in-memory dict: session_id -> list of messages.
#   Stored on app.state.sessions. Resets on server restart.
#   Phase 2 replaces this with Redis.

# ── Standard library ───────────────────────────────────────────────────────────
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

# ── Third-party ────────────────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Our code ───────────────────────────────────────────────────────────────────
from app.core.config import settings
from app.llm.factory import create_llm_provider
from app.agents.conversation import ConversationAgent
from app.agents.intent_classifier import IntentClassifier
from app.agents.compressor import CompressorAgent
from app.agents.aggregator import AggregatorAgent

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
    Startup (before yield): create LLM provider + agents, store on app.state.
    Shutdown (after yield): nothing to clean up in Phase 0.

    Route handlers access these via request.app.state.<name>.
    No module-level globals needed — everything flows through app.state.
    """
    logger.info("Starting up AnyMall-chan backend...")

    llm = create_llm_provider(settings)

    app.state.llm_provider = llm
    app.state.agent = ConversationAgent(llm=llm)
    app.state.intent_classifier = IntentClassifier(llm=llm)
    app.state.compressor = CompressorAgent(llm=llm)
    app.state.aggregator = AggregatorAgent()
    app.state.sessions = {}   # session_id -> list of messages (Phase 0 in-memory)

    logger.info("Backend ready. LLM provider: %s", settings.llm_provider)

    yield

    logger.info("Shutting down.")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AnyMall-chan API",
    description="Pet companion chat backend — Phase 0 MVP",
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
        "phase": "0",
    }
