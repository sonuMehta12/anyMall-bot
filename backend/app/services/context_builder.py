# app/services/context_builder.py
#
# Replaces dummy_context.py.
#
# Reads pet context from JSON files in data/ and returns the same 5 values
# that Agent 1 needs on every request:
#   (active_profile_dict, gap_list, pet_summary, pet_history, relationship_context)
#
# On first run (no JSON files exist), seeds with hardcoded Luna + Shara defaults
# so the system works identically to the old dummy_context.py.
#
# Called every request — reads from disk, no caching.
# Sub-millisecond for < 10 KB files. Cache invalidation complexity not worth it.
#
# Phase 1C: replace file reads with PostgreSQL queries. This module's public
# interface (build_context) stays the same — callers don't change.

import logging
from datetime import date

from app.storage.file_store import (
    read_pet_profile,
    write_pet_profile,
    read_active_profile,
    write_active_profile,
    read_user_profile,
    write_user_profile,
)
from constants import FULL_FIELD_LIST

logger = logging.getLogger(__name__)


# ── Seed defaults ─────────────────────────────────────────────────────────────
#
# Same data that was in dummy_context.py. Written to JSON files on first run.
# After that, JSON files are the source of truth.

_DEFAULT_PET_PROFILE: dict = {
    "pet_id": "luna-001",
    "name": "Luna",
    "species": "dog",
    "breed": "Shiba Inu",
    "date_of_birth": "2024-01-15",
    "sex": "female",
    "life_stage": "adult",
}

_DEFAULT_ACTIVE_PROFILE: dict = {
    "_pet_history": (
        "3 weeks ago: owner mentioned Luna had an ear infection; vet prescribed "
        "antibiotics. Last session: owner said Luna seemed less energetic than "
        "usual, but improving since starting the medication."
    ),
    "diet_type": {
        "value": "raw food",
        "confidence": 80,
    },
    "medications": {
        "value": "antibiotics (ear infection)",
        "confidence": 90,
    },
    "energy_level": {
        "value": "moderate",
        "confidence": 70,
    },
    "neutered_spayed": {
        "value": "yes",
        "confidence": 85,
    },
    "chronic_illness": {
        "value": "none",
        "confidence": 75,
    },
}

_DEFAULT_USER_PROFILE: dict = {
    "user_id": "shara-001",
    "pet_id": "luna-001",
    "session_count": 7,
    "relationship_summary": (
        "Owner (Shara) tends to be anxious. Prefers short replies. "
        "7 sessions total. Usually chats in evenings."
    ),
    "updated_at": "2025-01-01T00:00:00+00:00",
}

# Fields that come from PetProfile (onboarding) — always known, not gaps.
# These are merged into active_profile as high-confidence entries but excluded
# from gap_list computation because they are never "missing".
_IDENTITY_FIELDS: set[str] = {"name", "species", "breed", "age", "sex"}

_DEFAULT_PET_HISTORY: str = (
    "3 weeks ago: owner mentioned Luna had an ear infection; vet prescribed "
    "antibiotics. Last session: owner said Luna seemed less energetic than "
    "usual, but improving since starting the medication."
)

_DEFAULT_RELATIONSHIP_CONTEXT: str = (
    "Owner (Shara) tends to be anxious. Prefers short replies. "
    "7 sessions total. Usually chats in evenings."
)


# ── Age computation ───────────────────────────────────────────────────────────

def _compute_age_str(date_of_birth: str) -> str:
    """
    Compute a human-readable age string from an ISO date of birth.

    Returns "unknown age" if date_of_birth is "unknown" or unparseable.
    Returns "N months" if under 1 year, "N years" otherwise.
    """
    if date_of_birth == "unknown":
        return "unknown age"

    try:
        dob = date.fromisoformat(date_of_birth)
    except (ValueError, TypeError):
        return "unknown age"

    age_days = (date.today() - dob).days
    if age_days < 0:
        return "unknown age"
    if age_days < 365:
        months = max(age_days // 30, 1)
        return f"{months} months" if months > 1 else "1 month"

    years = age_days // 365
    return f"{years} years" if years > 1 else "1 year"


# ── Pet summary template ─────────────────────────────────────────────────────

def _build_pet_summary(pet_profile: dict, active_entries: dict) -> str:
    """
    Build a natural-language summary of the pet from structured data.

    No LLM — pure f-string template. Matches the shape of the old PET_SUMMARY
    from dummy_context.py.
    """
    name = pet_profile.get("name", "the pet")
    age_str = _compute_age_str(pet_profile.get("date_of_birth", "unknown"))
    breed = pet_profile.get("breed", "unknown breed")
    species = pet_profile.get("species", "pet")
    sex = pet_profile.get("sex", "unknown")

    parts = [f"{name} is a {age_str}-old"]

    if sex != "unknown":
        parts[0] = f"{name} is a {age_str}-old {sex}"

    parts[0] += f" {breed} ({species})"

    # Add dynamic facts from active_profile
    diet = active_entries.get("diet_type", {}).get("value")
    if diet:
        parts.append(f"on a {diet} diet")

    neutered = active_entries.get("neutered_spayed", {}).get("value")
    if neutered and neutered.lower() == "yes":
        parts.append("neutered/spayed")

    chronic = active_entries.get("chronic_illness", {}).get("value")
    if chronic and chronic.lower() != "none":
        parts.append(f"has {chronic}")
    elif chronic and chronic.lower() == "none":
        parts.append("no known chronic illness")

    meds = active_entries.get("medications", {}).get("value")
    if meds and meds.lower() not in ("none", ""):
        parts.append(f"currently on {meds}")

    energy = active_entries.get("energy_level", {}).get("value")
    if energy:
        parts.append(f"generally has {energy} energy level")

    # Join with periods
    summary = ". ".join(parts) + "."
    return summary


# ── Public API ────────────────────────────────────────────────────────────────

def build_context() -> tuple[dict, list[str], str, str, str]:
    """
    Build the 5 context values Agent 1 needs on every request.

    Returns:
        (active_profile_dict, gap_list, pet_summary, pet_history, relationship_context)

    Reads from data/*.json files. Seeds defaults on first run.
    Called every request — reads from disk, no caching.

    The returned active_profile_dict matches the shape conversation.py expects:
        {"field_name": {"value": "...", "confidence": N}, ...}
    """

    # ── 1. Read pet_profile.json ─────────────────────────────────────────────
    pet_profile = read_pet_profile()
    if pet_profile is None:
        logger.info("pet_profile.json not found — seeding with Luna defaults.")
        pet_profile = dict(_DEFAULT_PET_PROFILE)
        write_pet_profile(pet_profile)

    # ── 2. Read active_profile.json ──────────────────────────────────────────
    active_raw = read_active_profile()
    if active_raw is None:
        logger.info("active_profile.json not found — seeding with defaults.")
        active_raw = dict(_DEFAULT_ACTIVE_PROFILE)
        write_active_profile(active_raw)

    # ── 3. Read user_profile.json ────────────────────────────────────────────
    user_profile = read_user_profile()
    if user_profile is None:
        logger.info("user_profile.json not found — seeding with Shara defaults.")
        user_profile = dict(_DEFAULT_USER_PROFILE)
        write_user_profile(user_profile)

    # ── 4. Merge pet_profile static fields into active_profile ───────────────
    #
    # Agent 1 expects a single dict with ALL known facts — both static (name,
    # breed) and dynamic (diet, medications). We merge pet_profile fields as
    # high-confidence entries so the combined dict matches what Agent 1 has
    # always received from dummy_context.py.
    #
    # Read _pet_history via .get() — do NOT mutate active_raw.
    pet_history_raw = active_raw.get("_pet_history", _DEFAULT_PET_HISTORY)
    pet_history_str = pet_history_raw if isinstance(pet_history_raw, str) else _DEFAULT_PET_HISTORY

    # Start with active_profile dynamic entries (skip _-prefixed metadata keys)
    merged: dict = {}
    for key, entry in active_raw.items():
        if key.startswith("_"):
            continue
        if isinstance(entry, dict) and "value" in entry:
            merged[key] = entry

    # Add pet_profile static fields as high-confidence entries
    age_str = _compute_age_str(pet_profile.get("date_of_birth", "unknown"))

    static_fields = {
        "name": {"value": pet_profile.get("name", ""), "confidence": 100},
        "species": {"value": pet_profile.get("species", ""), "confidence": 100},
        "breed": {"value": pet_profile.get("breed", ""), "confidence": 90},
        "age": {"value": age_str, "confidence": 85},
        "sex": {"value": pet_profile.get("sex", "unknown"), "confidence": 100},
    }

    for key, entry in static_fields.items():
        if entry["value"] and entry["value"] != "unknown":
            merged[key] = entry

    # ── 5. Compute gap_list ──────────────────────────────────────────────────
    #
    # FULL_FIELD_LIST minus keys present in merged dict.
    # Exclude identity fields — they are always known from onboarding.
    present_keys = set(merged.keys())
    gap_list = [
        field for field in FULL_FIELD_LIST
        if field not in present_keys and field not in _IDENTITY_FIELDS
    ]

    # ── 6. Compute pet_summary ───────────────────────────────────────────────
    pet_summary = _build_pet_summary(pet_profile, merged)

    # ── 7. Read pet_history ──────────────────────────────────────────────────
    pet_history = pet_history_str

    # ── 8. Read relationship_context ─────────────────────────────────────────
    relationship_context = user_profile.get(
        "relationship_summary", _DEFAULT_RELATIONSHIP_CONTEXT
    )

    return merged, gap_list, pet_summary, pet_history, relationship_context
