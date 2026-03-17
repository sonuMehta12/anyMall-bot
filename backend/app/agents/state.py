# app/agents/state.py
#
# AgentState — shared context carrier for the background agent pipeline.
#
# Created once per request in chat.py before the chat handler runs.
# Passed to Compressor (and later Aggregator) so they can read what they need
# without being coupled to each other or to chat.py's internals.
#
# Design rules:
#   - Required fields are positional (no default) — enforces complete construction.
#   - Optional fields have safe defaults — nothing is None.
#   - Mutable fields use field(default_factory=...) — never share a list across instances.
#   - low_confidence_fields is REPLACED each turn, never appended across turns.
#     Callers must always assign a new list — never call .append() on it.

from dataclasses import dataclass, field


@dataclass
class PetInfo:
    """
    Identity fields for one pet — used by Compressor for fact attribution.

    In a dual-pet session, AgentState.pets has two PetInfo objects:
      pets[0] = Pet A (primary, always present)
      pets[1] = Pet B (secondary, optional)

    Fields match what AALDA provides + what the active_profile stores.
    """
    id: int
    name: str
    species: str = ""
    age: str = ""
    sex: str = ""       # "" if unknown — Compressor handles missing values gracefully
    weight: str = ""    # "" if unknown — same


@dataclass
class AgentState:
    """
    Shared context for the background agent pipeline (Compressor → Aggregator).

    Populated incrementally as the request moves through the pipeline:
      chat.py           sets: session_id, user_message, pets, recent_history
      ConversationAgent sets: is_entity, agent_reply
      Compressor        sets: extracted_facts, low_confidence_fields
      Aggregator        sets: profile_updated, fields_updated  (Phase 1C+)
    """

    # ── Set at request start — never modified ────────────────────────────────
    session_id: str
    thread_id: str     # Phase 2 — backend's thread UUID for DB writes
    user_message: str
    pets: list[PetInfo]  # index 0 = Pet A (always), index 1 = Pet B (if dual-pet)

    # ── Set by ConversationAgent after run() ─────────────────────────────────
    is_entity: bool = False       # True  → run Compressor
                                  # False → skip, no background task needed
    agent_reply: str = ""         # final reply sent to user (after guardrails)
    recent_history: list[dict] = field(default_factory=list)
    # last 6 messages (3 turns) from session history — used by Compressor
    # for pronoun resolution ("she", "her" → Luna)

    # ── Set by Compressor — REPLACED each turn, never accumulated ────────────
    extracted_facts: list = field(default_factory=list)  # list[ExtractedFact] — can't type here (circular import)
    # list of ExtractedFact objects with confidence > 0.70
    # passed to Aggregator to update active_profile

    low_confidence_fields: list[dict] = field(default_factory=list)
    # list of dicts: {"pet_name": str, "key": str, "value": str, "source_quote": str}
    # Facts where 0.50 <= confidence <= 0.70 — stored in pending_clarifications
    # ConversationAgent reads this next turn to ask a clarification question
    # IMPORTANT: always assign a new list here — never .append() across turns

    def __post_init__(self) -> None:
        if not self.pets:
            raise ValueError("AgentState requires at least one pet")

    # ── Convenience properties — backward compat for code that reads flat fields ──

    @property
    def pet_id(self) -> int:
        """Primary pet ID — used by _run_compaction() and other single-pet code."""
        return self.pets[0].id

    @property
    def is_dual_pet(self) -> bool:
        """True if this is a dual-pet session."""
        return len(self.pets) > 1
