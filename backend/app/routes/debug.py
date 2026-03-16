# app/routes/debug.py
#
# Debug endpoints — development only, remove in Phase 4.
#
# What lives here:
#   - GET /api/v1/debug/facts              — Compressor output (fact_log table)
#   - GET /api/v1/debug/profile            — Aggregator output (active_profile table)
#   - GET /api/v1/debug/threads            — Active threads (Phase 2)
#   - GET /api/v1/debug/thread/{id}/messages — Messages for a thread (Phase 2)
#
# All pet-specific endpoints require pet_id query param.
# Phase 1C: reads from PostgreSQL instead of JSON files.

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.db.session import get_session
from app.db.repositories import FactLogRepo, ActiveProfileRepo, ThreadRepo, ThreadMessageRepo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


@router.get("/facts", summary="Compressor output — recent extracted facts")
async def debug_facts(
    pet_id: int = 0,
    session_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Returns the most recent entries from the fact_log table.

    Query params:
        pet_id      — which pet (required)
        session_id  — filter to one session (omit for all sessions)
        limit       — max entries to return (default 20, max 100)
    """
    if pet_id == 0:
        raise HTTPException(status_code=400, detail="pet_id query parameter is required.")

    limit = min(limit, 100)

    async with get_session() as session:
        repo = FactLogRepo(session)
        facts = await repo.read_recent(pet_id, session_id=session_id, limit=limit)

    return {
        "count": len(facts),
        "pet_id": pet_id,
        "session_id_filter": session_id,
        "facts": facts,
    }


@router.get("/profile", summary="Active profile — current best-known facts")
async def debug_profile(pet_id: int = 0) -> dict[str, Any]:
    """
    Returns the current active_profile from the database.

    Query params:
        pet_id — which pet (required)
    """
    if pet_id == 0:
        raise HTTPException(status_code=400, detail="pet_id query parameter is required.")

    async with get_session() as session:
        repo = ActiveProfileRepo(session)
        profile = await repo.read_all(pet_id)

    if profile is None:
        return {"status": "no_profile", "field_count": 0, "profile": {}}

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
