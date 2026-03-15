# app/routes/debug.py
#
# Debug endpoints — development only, remove in Phase 4.
#
# What lives here:
#   - GET /debug/facts              — Compressor output (fact_log table)
#   - GET /debug/profile            — Aggregator output (active_profile table)
#   - GET /debug/threads            — Active threads (Phase 2)
#   - GET /debug/thread/{id}/messages — Messages for a thread (Phase 2)
#
# GET /confidence lives in main.py (must be defined directly on the app
# to take priority over the catch-all frontend route).
#
# Phase 1C: reads from PostgreSQL instead of JSON files.

import logging
from typing import Any

from fastapi import APIRouter

from app.db.session import get_session
from app.db.repositories import FactLogRepo, ActiveProfileRepo, ThreadRepo, ThreadMessageRepo
from constants import DEFAULT_PET_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/facts", summary="Compressor output — recent extracted facts")
async def debug_facts(
    session_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Returns the most recent entries from the fact_log table.

    Query params:
        session_id  — filter to one session (omit for all sessions)
        limit       — max entries to return (default 20, max 100)

    This is how the UI sees Agent 2 (Compressor) output.
    The Compressor runs after the /chat reply is sent, so its output
    is NOT in the /chat response — poll this endpoint instead.
    """
    limit = min(limit, 100)

    async with get_session() as session:
        repo = FactLogRepo(session)
        facts = await repo.read_recent(DEFAULT_PET_ID, session_id=session_id, limit=limit)

    return {
        "count": len(facts),
        "session_id_filter": session_id,
        "facts": facts,
    }


@router.get("/profile", summary="Active profile — current best-known facts")
async def debug_profile() -> dict[str, Any]:
    """
    Returns the current active_profile from the database.

    This is how you see Agent 3 (Aggregator) output.
    After each /chat with extractable facts, the Aggregator merges
    high-confidence facts into the active profile.
    """
    async with get_session() as session:
        repo = ActiveProfileRepo(session)
        profile = await repo.read_all(DEFAULT_PET_ID)

    if profile is None:
        return {"status": "no_profile", "field_count": 0, "profile": {}}

    # Count only fact entries (skip metadata keys like _pet_history).
    fact_count = sum(1 for k in profile if not k.startswith("_"))
    return {"status": "ok", "field_count": fact_count, "profile": profile}


@router.get("/threads", summary="Active threads")
async def debug_threads() -> dict[str, Any]:
    """Returns all active threads from PostgreSQL."""
    async with get_session() as session:
        repo = ThreadRepo(session)
        threads = await repo.get_all_active()

    return {"count": len(threads), "threads": threads}


@router.get("/thread/{thread_id}/messages", summary="Thread messages")
async def debug_thread_messages(thread_id: str) -> dict[str, Any]:
    """Returns all messages for a thread from PostgreSQL."""
    async with get_session() as session:
        repo = ThreadMessageRepo(session)
        messages = await repo.read_thread(thread_id)

    return {"thread_id": thread_id, "count": len(messages), "messages": messages}
