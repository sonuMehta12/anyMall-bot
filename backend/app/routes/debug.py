# app/routes/debug.py
#
# Debug endpoints — development only, remove in Phase 4.
#
# What lives here:
#   - GET /debug/facts   — Compressor output (fact_log.json)
#   - GET /debug/profile — Aggregator output (active_profile.json)
#
# These endpoints read directly from JSON files — no shared state needed.

import logging
from typing import Any

from fastapi import APIRouter

from app.storage.file_store import read_fact_log, read_active_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/facts", summary="Compressor output — recent extracted facts")
async def debug_facts(
    session_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Returns the most recent entries from data/fact_log.json.

    Query params:
        session_id  — filter to one session (omit for all sessions)
        limit       — max entries to return (default 20, max 100)

    This is how the UI sees Agent 2 (Compressor) output.
    The Compressor runs after the /chat reply is sent, so its output
    is NOT in the /chat response — poll this endpoint instead.
    """
    limit = min(limit, 100)
    all_facts = read_fact_log()

    if session_id:
        all_facts = [f for f in all_facts if f.get("session_id") == session_id]

    recent = all_facts[-limit:]   # most recent N entries
    recent.reverse()              # newest first

    return {
        "count": len(recent),
        "session_id_filter": session_id,
        "facts": recent,
    }


@router.get("/profile", summary="Active profile — current best-known facts")
async def debug_profile() -> dict[str, Any]:
    """
    Returns the current active_profile.json contents.

    This is how you see Agent 3 (Aggregator) output.
    After each /chat with extractable facts, the Aggregator merges
    high-confidence facts into the active profile.
    """
    profile = read_active_profile()
    if profile is None:
        return {"status": "no_profile", "field_count": 0, "profile": {}}

    # Count only fact entries (skip metadata keys like _pet_history).
    fact_count = sum(1 for k in profile if not k.startswith("_"))
    return {"status": "ok", "field_count": fact_count, "profile": profile}
