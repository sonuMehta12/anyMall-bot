# app/types.py
#
# Shared type definitions used across multiple layers (agents, services, db).
#
# Why a separate file?
#   ActiveProfileEntry is created in aggregator.py, stored via repositories.py,
#   and consumed in context_builder.py + conversation.py.  A central definition
#   prevents circular imports and gives every layer the same type contract.

from typing import TypedDict


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
