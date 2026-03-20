# app/routes/debug.py
#
# Debug endpoints — development only, remove in Phase 4.
#
# What lives here:
#   - GET  /api/v1/debug/facts              — Compressor output (fact_log table)
#   - GET  /api/v1/debug/profile            — Aggregator output (active_profile table)
#   - GET  /api/v1/debug/threads            — Active threads (Phase 2)
#   - GET  /api/v1/debug/thread/{id}/messages — Messages for a thread (Phase 2)
#   - GET  /api/v1/debug/user              — User record by user_code (W18)
#   - GET  /api/v1/debug/clarifications    — Pending clarifications (in-memory)
#   - POST /api/v1/debug/trigger_nightly   — Sprint 6: run nightly jobs immediately (testing)
#   - POST /api/v1/debug/trigger_summarizer — Sprint 6: run ThreadSummarizer on a thread (testing)
#
# All pet-specific endpoints require pet_id query param.
# Phase 1C: reads from PostgreSQL instead of JSON files.

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.db.session import get_session
from app.db.repositories import FactLogRepo, ActiveProfileRepo, ThreadRepo, ThreadMessageRepo, UserRepo
from app.jobs.nightly import run_nightly_jobs

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


@router.get("/user", summary="User record")
async def debug_user(user_code: str = "") -> dict[str, Any]:
    """
    Returns a user record by user_code.

    Query params:
        user_code — the X-User-Code value (required)
    """
    if not user_code:
        raise HTTPException(status_code=400, detail="user_code query parameter is required.")

    async with get_session() as session:
        repo = UserRepo(session)
        user = await repo.read(user_code)

    if user is None:
        return {"status": "not_found", "user": None}
    return {"status": "ok", "user": user}


@router.get("/clarifications", summary="Pending clarifications (in-memory)")
async def debug_clarifications(request: Request, thread_id: str | None = None) -> dict[str, Any]:
    """
    Returns pending_clarifications from app.state (in-memory only).

    Query params:
        thread_id — filter to one thread (omit for all threads)
    """
    pending = getattr(request.app.state, "pending_clarifications", {})
    if thread_id:
        items = pending.get(thread_id, [])
        return {"thread_id": thread_id, "count": len(items), "clarifications": items}
    return {"thread_count": len(pending), "all": pending}


@router.post("/trigger_nightly", summary="Sprint 6 testing: run nightly jobs immediately")
async def debug_trigger_nightly(request: Request) -> dict[str, Any]:
    """
    Runs run_nightly_jobs() immediately against the live app.state.

    Used by test_sprint6.py to test closing summary + relationship summary jobs
    without waiting until midnight UTC.

    Returns a summary of what ran.
    """
    try:
        await run_nightly_jobs(request.app.state)
        return {"status": "ok", "message": "Nightly jobs completed — check server logs for details"}
    except Exception as exc:
        logger.error("debug_trigger_nightly: error — %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/trigger_summarizer", summary="Sprint 6 testing: run ThreadSummarizer on a thread")
async def debug_trigger_summarizer(request: Request, thread_id: str = "") -> dict[str, Any]:
    """
    Runs ThreadSummarizer on a given thread_id and writes the result to
    threads.compaction_summary.

    Used by test_sprint6.py to verify the two-section HEALTH CONTEXT / USER STYLE
    format without needing to send 50 messages to trigger natural compaction.

    Query params:
        thread_id — which thread to summarize (required)
    """
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id query parameter is required.")

    summarizer = getattr(request.app.state, "thread_summarizer", None)
    if summarizer is None:
        raise HTTPException(status_code=503, detail="thread_summarizer not initialised")

    async with get_session() as db_session:
        msg_repo = ThreadMessageRepo(db_session)
        messages = await msg_repo.read_thread(thread_id)

    if not messages:
        raise HTTPException(status_code=404, detail=f"No messages found for thread_id={thread_id!r}")

    async with get_session() as db_session:
        thread_repo = ThreadRepo(db_session)
        thread = await thread_repo.get_by_thread_id(thread_id)

    existing_summary = thread.get("compaction_summary") if thread else None
    summary = await summarizer.summarize(messages, existing_summary)

    async with get_session() as db_session:
        thread_repo = ThreadRepo(db_session)
        await thread_repo.update_compaction_summary(thread_id, summary)

    return {
        "status": "ok",
        "thread_id": thread_id,
        "message_count": len(messages),
        "summary": summary,
    }
