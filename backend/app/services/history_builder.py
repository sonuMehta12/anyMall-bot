# app/services/history_builder.py
#
# HistoryBuilder — builds a pet health history narrative from fact_log entries.
#
# What it does:
#   Reads high-confidence facts from fact_log since the last history build,
#   merges them with the existing _pet_history narrative using an LLM,
#   and returns an updated 3-6 sentence plain-text narrative.
#
# Where it writes:
#   active_profile._pet_history (via ActiveProfileRepo.write_history in background.py)
#
# Trigger (hybrid, in background.py _maybe_run_history_builder):
#   Condition A: >= 3 new high-confidence facts in this session
#   Condition B: >= 1 new fact AND >= 2 sessions since last build
#
# Separate pipeline from RelationshipBuilder:
#   HistoryBuilder reads from fact_log (structured data).
#   RelationshipBuilder reads from threads.compaction_summary (prose USER STYLE sections).
#   They never share inputs.

# ── Standard library ────────────────────────────────────────────────────────
import logging
from datetime import datetime, timezone

# ── Our code ────────────────────────────────────────────────────────────────
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Model configuration ────────────────────────────────────────────────────────
# Model to use for this service. None = use provider default (set in .env).
# Change this to test a specific model, e.g. "gpt-5.4-nano".
# See design-docs/model-strategy.md for full rationale.
_MODEL: str | None = None

# ── Facts included in history narrative ────────────────────────────────────
# These field_key values represent health-influencing events.
# Stable current-state fields (e.g. diet_type without change context) are
# handled by active_profile, not the history narrative.
_HEALTH_RELEVANT_KEYS = {
    # Diagnoses and medical events
    "diagnosis", "condition", "injury", "illness", "surgery", "procedure",
    # Symptoms
    "symptom", "symptoms", "vomiting", "diarrhea", "lethargy", "limping",
    "scratching", "coughing", "sneezing",
    # Medications
    "medication", "medications", "drug", "prescription", "antibiotic",
    "supplement", "treatment",
    # Vet visits
    "vet_visit", "vet_appointment", "checkup", "vaccination",
    # Weight and body condition
    "weight", "body_condition",
    # Energy and behavior signals
    "energy_level", "activity_level", "appetite", "water_intake",
    # Diet changes (the change event — not the stable current diet_type)
    "diet_change", "food_change", "diet_transition",
}


HISTORY_SYSTEM_PROMPT = """You are a veterinary health history writer for a pet companion app.

Your job is to merge new pet health facts into an existing history narrative.

RULES:
1. Include only health-influencing events: diagnoses, symptoms, injuries, medications
   (start or stop), weight changes, energy level changes, diet transitions,
   behavioral changes suggesting health status, vet visits and procedures.
2. Exclude stable current states (e.g. "currently eats raw food" unless there was a change),
   owner preferences, and facts with no time dimension.
3. Use time-aware language based on the extracted_at timestamps provided.
   Convert timestamps to relative language: "3 weeks ago", "last week", "recently", "today".
4. Note trends when multiple data points show a pattern: "energy has been trending low
   over the past two sessions".
5. Merge with existing history — do not lose older context. Integrate, don't replace.
6. Output plain text only, 3-6 sentences. No bullet points, no headers, no markdown.
7. If there are no health-relevant facts, return the existing history unchanged.
   If there is no existing history, write one from the new facts only.
"""


class HistoryBuilder:
    """
    Builds and maintains a pet health history narrative from fact_log entries.

    Receives LLMProvider via constructor (same strategy pattern as all agents).
    Temperature 0.0 for deterministic output.
    """

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def build(
        self,
        pet_id: int,
        new_facts: list[dict],
        existing_history: str,
    ) -> str:
        """
        Merge new facts into the existing history narrative.

        Args:
            pet_id: For logging only.
            new_facts: List of fact dicts from FactLogRepo.read_since()
                       (field_key, value, confidence, extracted_at, session_id, etc.)
            existing_history: Current active_profile._pet_history (may be "").

        Returns:
            Updated narrative string (3-6 plain-text sentences).
            Returns existing_history unchanged if no health-relevant facts found.
        """
        # Filter to health-relevant facts only.
        # FactLog.to_dict() returns "key" (not "field_key") — match that shape.
        health_facts = [
            f for f in new_facts
            if f.get("key", "") in _HEALTH_RELEVANT_KEYS
        ]

        if not health_facts:
            logger.debug(
                "HistoryBuilder: no health-relevant facts — returning existing history (pet_id=%s)",
                pet_id,
            )
            return existing_history

        # Build the user prompt
        now = datetime.now(timezone.utc)
        facts_lines = []
        for f in health_facts:
            extracted_at = f.get("extracted_at", "")
            relative_time = _relative_time(extracted_at, now)
            facts_lines.append(
                f"- [{relative_time}] {f.get('key', '')}: {f.get('value', '')} "
                f"(confidence={f.get('confidence', 0):.2f}, quote={f.get('source_quote', '')!r})"
            )

        user_prompt = ""
        if existing_history:
            user_prompt += f"Existing history:\n{existing_history}\n\n---\n\n"
        user_prompt += (
            f"New health facts to incorporate ({len(health_facts)} of {len(new_facts)} facts "
            f"are health-relevant):\n"
            + "\n".join(facts_lines)
            + "\n\nPlease provide an updated health history narrative."
        )

        result = await self._llm.complete(
            system_prompt=HISTORY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=500,    # 3-6 sentence narrative needs headroom
            model=_MODEL,      # None = provider default; set above to test a model
        )

        narrative = result.strip()
        logger.info(
            "HistoryBuilder: updated narrative for pet_id=%s (health_facts=%d total_facts=%d)",
            pet_id, len(health_facts), len(new_facts),
        )
        return narrative


def _relative_time(extracted_at: str, now: datetime) -> str:
    """
    Convert an ISO timestamp to a human-readable relative string.

    Examples: "today", "yesterday", "3 days ago", "2 weeks ago", "1 month ago".
    Falls back to the raw string if parsing fails.
    """
    if not extracted_at:
        return "recently"
    try:
        dt = datetime.fromisoformat(extracted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        days = delta.days
        if days == 0:
            return "today"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days} days ago"
        weeks = days // 7
        if weeks == 1:
            return "last week"
        if weeks < 5:
            return f"{weeks} weeks ago"
        months = days // 30
        if months == 1:
            return "last month"
        return f"{months} months ago"
    except (ValueError, TypeError):
        return extracted_at
