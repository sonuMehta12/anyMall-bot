# app/types.py
#
# Shared type definitions used across multiple layers (agents, services, db).
#
# Why a separate file?
#   ActiveProfileEntry is created in aggregator.py, stored via repositories.py,
#   and consumed in context_builder.py + conversation.py.  A central definition
#   prevents circular imports and gives every layer the same type contract.
#
#   StateBag is the type contract for FastAPI's app.state object, used by
#   background pipeline functions (_run_background, _run_compaction) so they
#   don't need `Any` type hints.

import asyncio
from typing import Any, Protocol, TypedDict


class ActiveProfileEntry(TypedDict, total=False):
    """
    One fact entry in an active_profile dict.

    Example:
        {"value": "raw food", "confidence": 0.80, "source_rank": "explicit_owner",
         "time_scope": "current", "source_quote": "I feed him raw food",
         "updated_at": "2026-03-16T12:00:00+00:00", "session_id": "abc-123",
         "status": "new", "change_detected": "", "trend_flag": ""}

    `total=False` means all keys are optional at construction time —
    static fields (from pet_profile) only have value + confidence,
    while Aggregator-produced entries have all 10 fields.
    The `value` key is always present in practice.
    """
    value: str
    confidence: float
    source_rank: str
    time_scope: str
    source_quote: str
    updated_at: str
    session_id: str
    status: str
    change_detected: str
    trend_flag: str


class StateBag(Protocol):
    """
    Type contract for FastAPI's app.state object.

    Populated in main.py lifespan(). Accessed by background pipeline functions
    in chat.py / background.py. Using a Protocol instead of Any gives static
    type checkers visibility into what attributes exist.
    """
    sessions: dict[str, list]
    session_meta: dict[str, dict]
    compaction_in_progress: set[str]
    thread_locks: dict[str, asyncio.Lock]
    pet_locks: dict[int, asyncio.Lock]
    background_tasks: set[asyncio.Task]
    pending_clarifications: dict[str, list]
    agent: Any
    intent_classifier: Any
    compressor: Any
    aggregator: Any
    thread_summarizer: Any
    pet_fetcher: Any
    llm_provider: Any
