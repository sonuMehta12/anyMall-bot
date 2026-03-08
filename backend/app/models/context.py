# app/models/context.py
#
# Data model classes for pet context — the structured data that flows
# through the agent pipeline.
#
# Three dataclasses, each mirroring a future PostgreSQL table:
#   PetProfile         → `pet` table (static identity, set at onboarding)
#   ActiveProfileEntry → `active_profile` table rows (dynamic facts, one per key)
#   UserProfile        → `user_profile` table (owner relationship data)
#
# Each has to_dict() for JSON serialisation and from_dict() for deserialisation.
# ActiveProfileEntry.from_dict() handles two formats:
#   - Simple seed: {"value": "raw food", "confidence": 80}
#   - Full Aggregator output: all 11 fields present

from dataclasses import dataclass, asdict
from datetime import datetime, timezone


# ── PetProfile ────────────────────────────────────────────────────────────────

@dataclass
class PetProfile:
    """
    Static pet identity — set at onboarding, rarely changes.

    Maps to the `pet` table in Phase 1C PostgreSQL.
    Fields like name, species, breed are known from signup and almost never
    updated by the Aggregator.
    """
    pet_id: str
    name: str
    species: str          # "dog" | "cat"
    breed: str
    date_of_birth: str    # ISO date (e.g. "2024-01-15") or "unknown"
    sex: str              # "male" | "female" | "unknown"
    life_stage: str       # "puppy" | "adult" | "senior"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PetProfile":
        return cls(
            pet_id=str(data.get("pet_id", "")),
            name=str(data.get("name", "")),
            species=str(data.get("species", "")),
            breed=str(data.get("breed", "")),
            date_of_birth=str(data.get("date_of_birth", "unknown")),
            sex=str(data.get("sex", "unknown")),
            life_stage=str(data.get("life_stage", "adult")),
        )


# ── ActiveProfileEntry ────────────────────────────────────────────────────────

@dataclass
class ActiveProfileEntry:
    """
    One dynamic fact about a pet in the active profile.

    Maps to one row in the `active_profile` table (PK: pet_id + key).
    The Aggregator creates/updates these when merging Compressor output.

    Fields added from the PRD conflict resolution design:
      status          — "new" | "updated" | "confirmed"
      change_detected — "" or human-readable change description
      trend_flag      — "" or pattern signal like "declining_energy"
    """
    key: str
    value: str
    confidence: float       # 0.0–1.0
    source_rank: str        # "vet_record" | "explicit_owner" | "user_correction"
    time_scope: str         # "current" | "past" | "unknown"
    source_quote: str
    updated_at: str         # ISO datetime — when this entry was written/updated
    session_id: str
    status: str             # "new" | "updated" | "confirmed"
    change_detected: str    # "" or "decreased_from_moderate_to_low"
    trend_flag: str         # "" or "declining_energy"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, key: str = "") -> "ActiveProfileEntry":
        """
        Build an ActiveProfileEntry from a dict.

        Handles two formats:
          - Simple seed: {"value": "raw food", "confidence": 80}
            Missing fields get sensible defaults.
          - Full Aggregator output: all 11 fields present.

        The `key` param is used when the key lives outside the dict
        (e.g. active_profile.json is keyed by field name).
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # Confidence: support both 0-1 float and 0-100 int formats.
        raw_conf = data.get("confidence", 0.0)
        confidence = float(raw_conf)
        if confidence > 1.0:
            confidence = confidence / 100.0

        return cls(
            key=str(data.get("key", key)),
            value=str(data.get("value", "")),
            confidence=confidence,
            source_rank=str(data.get("source_rank", "explicit_owner")),
            time_scope=str(data.get("time_scope", "current")),
            source_quote=str(data.get("source_quote", "")),
            updated_at=str(data.get("updated_at", now_iso)),
            session_id=str(data.get("session_id", "")),
            status=str(data.get("status", "new")),
            change_detected=str(data.get("change_detected", "")),
            trend_flag=str(data.get("trend_flag", "")),
        )


# ── UserProfile ───────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """
    Owner relationship data — how to communicate with this user.

    Maps to the `user_profile` table in Phase 1C PostgreSQL.
    In Phase 1B, relationship_summary is hardcoded (seeded on first run).
    In Phase 2, it is rebuilt by LLM compaction across sessions.
    """
    user_id: str
    pet_id: str
    session_count: int
    relationship_summary: str    # NL text: "Owner tends to be anxious..."
    updated_at: str              # ISO datetime

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        now_iso = datetime.now(timezone.utc).isoformat()
        return cls(
            user_id=str(data.get("user_id", "")),
            pet_id=str(data.get("pet_id", "")),
            session_count=int(data.get("session_count", 0)),
            relationship_summary=str(data.get("relationship_summary", "")),
            updated_at=str(data.get("updated_at", now_iso)),
        )
