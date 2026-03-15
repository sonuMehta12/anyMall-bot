# app/agents/state.py
#
# AgentState — shared context carrier for the background agent pipeline.
#
# Created once per request in main.py before the chat handler runs.
# Passed to Compressor (and later Aggregator) so they can read what they need
# without being coupled to each other or to main.py's internals.
#
# Design rules:
#   - Required fields are positional (no default) — enforces complete construction.
#   - Optional fields have safe defaults — nothing is None.
#   - Mutable fields use field(default_factory=...) — never share a list across instances.
#   - low_confidence_fields is REPLACED each turn, never appended across turns.
#     Callers must always assign a new list — never call .append() on it.

from dataclasses import dataclass, field


@dataclass
class AgentState:
    """
    Shared context for the background agent pipeline (Compressor → Aggregator).

    Populated incrementally as the request moves through the pipeline:
      main.py         sets: session_id, user_message, pet_*, recent_history
      ConversationAgent sets: is_entity, agent_reply
      Compressor        sets: extracted_facts, low_confidence_fields
      Aggregator        sets: profile_updated, fields_updated  (Phase 1C+)
    """

    # ── Set at request start — never modified ────────────────────────────────
    session_id: str
    thread_id: str     # Phase 2 — backend's thread UUID for DB writes
    user_message: str
    pet_name: str
    pet_species: str
    pet_age: str
    pet_sex: str       # "" if unknown — Compressor handles missing values gracefully
    pet_weight: str    # "" if unknown — same

    # ── Set by ConversationAgent after run() ─────────────────────────────────
    is_entity: bool = False       # True  → run Compressor
                                  # False → skip, no background task needed
    agent_reply: str = ""         # final reply sent to user (after guardrails)
    recent_history: list = field(default_factory=list)
    # last 6 messages (3 turns) from session history — used by Compressor
    # for pronoun resolution ("she", "her" → Luna)

    # ── Set by Compressor — REPLACED each turn, never accumulated ────────────
    extracted_facts: list = field(default_factory=list)
    # list of ExtractedFact objects with confidence > 0.70
    # passed to Aggregator to update active_profile

    low_confidence_fields: list[str] = field(default_factory=list)
    # field keys where 0.50 <= confidence <= 0.70
    # ConversationAgent reads this next turn to ask a clarification question
    # IMPORTANT: always assign a new list here — never .append() across turns
