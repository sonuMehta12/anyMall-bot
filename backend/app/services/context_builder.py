# app/services/context_builder.py
#
# Builds context values Agent 1 needs on every request.
#
# build_pet_context() — builds context for ONE pet:
#   (active_profile, gap_list, pet_info_json, pet_summary, pet_history)
#
# Pet data comes from:
#   - pet_profile: static identity from AALDA API (via pet_fetcher.py)
#   - aalda_facts: dynamic facts from AALDA (nutrition, diet, vaccinations)
#   - active_profile_raw: learned facts from DB (via Compressor/Aggregator pipeline)
#
# No defaults. No seeding. No fallbacks. If AALDA is down → error.

import json
import logging
from constants import FULL_FIELD_LIST
from app.services.pet_fetcher import compute_current_age
from app.types import ActiveProfileEntry

logger = logging.getLogger(__name__)


# Fields that come from PetProfile (onboarding) — always known, not gaps.
_IDENTITY_FIELDS: set[str] = {"name", "species", "breed", "age", "sex"}


# ── Age computation ───────────────────────────────────────────────────────────

def _compute_age_str(date_of_birth: str) -> str:
    """
    Compute a human-readable age string from an ISO date of birth.

    Delegates to compute_current_age() in pet_fetcher.py (the single
    implementation) and strips the trailing " old" suffix so callers
    can use it in "a {age_str}-old" constructions without doubling "old".
    """
    age = compute_current_age(date_of_birth)
    if age.endswith(" old"):
        return age[:-4]  # "9 months old" → "9 months"
    return age


# ── Pet summary template ─────────────────────────────────────────────────────

def _build_pet_summary(pet_profile: dict, active_entries: dict[str, ActiveProfileEntry]) -> str:
    """
    Build a natural-language summary of the pet from structured data.

    No LLM — pure f-string template.
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

    summary = ". ".join(parts) + "."
    return summary


# ── Pet info JSON (for v0.3 prompt) ───────────────────────────────────────────

def _build_pet_info_json(pet_profile: dict, aalda_facts: dict[str, ActiveProfileEntry]) -> str:
    """
    Build the pet_info JSON string for the v0.3 prompt template.

    The prompt expects a JSON object with fields like name, species, breed,
    sex, current_age, is_neutered, activity_level, diet, vaccinations, etc.
    """
    dob = pet_profile.get("date_of_birth", "unknown")
    current_age = compute_current_age(dob)

    info = {
        "name": pet_profile.get("name", ""),
        "species": pet_profile.get("species", ""),
        "breed": pet_profile.get("breed", ""),
        "sex": pet_profile.get("sex", "unknown"),
        "current_age": current_age,
    }

    # Add AALDA facts if available
    neutered = aalda_facts.get("neutered_spayed", {})
    if neutered:
        info["is_neutered"] = neutered.get("value", "") == "yes"

    activity = aalda_facts.get("activity_level", {})
    if activity:
        info["activity_level"] = activity.get("value", "")

    bcs = aalda_facts.get("body_condition_score", {})
    if bcs:
        info["body_condition_score"] = bcs.get("value", "")

    diet = aalda_facts.get("diet_type", {})
    if diet:
        info["diet"] = diet.get("value", "")

    food_brand = aalda_facts.get("food_brand", {})
    if food_brand:
        info["food_brand"] = food_brand.get("value", "")

    vaccinations = aalda_facts.get("vaccinations", {})
    if vaccinations:
        info["vaccinations"] = vaccinations.get("value", "")

    return json.dumps(info, ensure_ascii=False, indent=2)


# ── Public API ────────────────────────────────────────────────────────────────

def build_pet_context(
    pet_profile: dict,
    aalda_facts: dict[str, ActiveProfileEntry],
    active_profile_raw: dict[str, ActiveProfileEntry | str] | None,
) -> dict:
    """
    Build context for ONE pet.

    Args:
        pet_profile: static identity from AALDA (name, species, breed, etc.)
        aalda_facts: dynamic facts from AALDA (neutered, diet, vaccinations)
        active_profile_raw: learned facts from DB (Compressor/Aggregator output)

    Returns:
        dict with keys:
          active_profile: merged dict of all known facts
          gap_list: list of field names we don't know yet
          pet_info_json: JSON string for v0.3 prompt
          pet_summary: NL paragraph for deeplink context
          pet_history: chronological narrative from _pet_history (or "")
    """
    active_raw = active_profile_raw or {}

    # ── 0. Extract _pet_history (special key — raw string, not a fact dict)
    pet_history = active_raw.get("_pet_history", "")
    if not isinstance(pet_history, str):
        pet_history = ""

    # ── 1. Start with AALDA facts as BASE layer (registration data)
    merged: dict = {}
    for key, entry in aalda_facts.items():
        if isinstance(entry, dict) and "value" in entry:
            merged[key] = entry

    # ── 2. Layer DB-learned facts on top (chat wins for overlapping fields)
    #    The user told our bot something more recent than what AALDA has.
    #    e.g. user says "switched to raw food" → DB has diet_type="raw food"
    #         but AALDA still has diet_type="kibble" from months ago.
    #    Chat-learned facts must take priority.
    for key, entry in active_raw.items():
        if key.startswith("_"):
            continue
        if isinstance(entry, dict) and "value" in entry:
            merged[key] = entry

    # ── 3. Add pet_profile static fields as high-confidence entries
    age_str = _compute_age_str(pet_profile.get("date_of_birth", "unknown"))

    static_fields = {
        "name": {"value": pet_profile.get("name", ""), "confidence": 1.0},
        "species": {"value": pet_profile.get("species", ""), "confidence": 1.0},
        "breed": {"value": pet_profile.get("breed", ""), "confidence": 0.95},
        "age": {"value": age_str, "confidence": 0.90},
        "sex": {"value": pet_profile.get("sex", "unknown"), "confidence": 1.0},
    }

    for key, entry in static_fields.items():
        if entry["value"] and entry["value"] != "unknown":
            merged[key] = entry

    # ── 4. Compute gap_list
    present_keys = set(merged.keys())
    gap_list = [
        field for field in FULL_FIELD_LIST
        if field not in present_keys and field not in _IDENTITY_FIELDS
    ]

    # ── 5. Build pet_summary (for deeplink context)
    pet_summary = _build_pet_summary(pet_profile, merged)

    # ── 6. Build pet_info JSON (for v0.3 prompt)
    pet_info_json = _build_pet_info_json(pet_profile, aalda_facts)

    return {
        "active_profile": merged,
        "gap_list": gap_list,
        "pet_info_json": pet_info_json,
        "pet_summary": pet_summary,
        "pet_history": pet_history,
    }
