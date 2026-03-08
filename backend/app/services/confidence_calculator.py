# app/services/confidence_calculator.py
#
# Confidence bar calculator — pure arithmetic, no LLM calls.
#
# Computes a 0-100 score representing how well AnyMall-chan knows the pet.
# Three signals per field:
#   1. Per-field confidence  (from Compressor/Aggregator, 0.0-1.0)
#   2. Time decay            (exponential, based on updated_at age + life stage)
#   3. Importance weight     (Tier A=3, B=2, C=1)
#
# Formula:
#   score = sum(field_confidence * decay * weight) / total_weight * 100
#
# Called synchronously per /chat request — sub-millisecond on 22 fields.

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Field importance tiers ───────────────────────────────────────────────────
#
# 22 scored fields.  "name" is excluded (always known from onboarding).
#
# Tier A (weight 3): core identity + health — can't give good advice without these.
# Tier B (weight 2): useful context that affects recommendations.
# Tier C (weight 1): bonus detail, not critical for most interactions.

TIER_A: set[str] = {
    "species", "breed", "age", "weight",
    "diet_type", "medications", "chronic_illness", "allergies",
}

TIER_B: set[str] = {
    "sex", "neutered_spayed", "energy_level", "appetite",
    "vaccinations", "past_conditions", "food_brand",
}

TIER_C: set[str] = {
    "temperament", "behavioral_traits", "activity_level",
    "vet_name", "last_vet_visit", "microchipped",
    "insurance", "past_medications",
}

FIELD_WEIGHTS: dict[str, int] = {}
for _f in TIER_A:
    FIELD_WEIGHTS[_f] = 3
for _f in TIER_B:
    FIELD_WEIGHTS[_f] = 2
for _f in TIER_C:
    FIELD_WEIGHTS[_f] = 1

# Fixed denominator — missing fields contribute 0 to numerator but
# still count toward the max.  (8*3) + (7*2) + (8*1) = 46.
TOTAL_WEIGHT: int = sum(FIELD_WEIGHTS.values())


# ── Decay categories ────────────────────────────────────────────────────────
#
# Each field belongs to one decay category.  Static fields never decay
# (breed doesn't change).  Fast fields lose relevance quickly (weight
# for a growing puppy).

STATIC_FIELDS: set[str] = {"species", "breed", "sex", "neutered_spayed", "microchipped"}
SLOW_FIELDS: set[str]   = {"allergies", "chronic_illness", "temperament", "behavioral_traits", "insurance"}
MEDIUM_FIELDS: set[str]  = {"diet_type", "food_brand", "medications", "vaccinations", "vet_name", "last_vet_visit"}
FAST_FIELDS: set[str]    = {"weight", "age", "energy_level", "appetite", "activity_level", "past_conditions", "past_medications"}

# Base half-life in days per category.
# "static" uses 0 as a sentinel — _compute_decay returns 1.0 immediately.
BASE_HALF_LIVES: dict[str, float] = {
    "static": 0.0,
    "slow":   180.0,
    "medium": 90.0,
    "fast":   45.0,
}


def _decay_category(field: str) -> str:
    """Return the decay category for a field name."""
    if field in STATIC_FIELDS:
        return "static"
    if field in SLOW_FIELDS:
        return "slow"
    if field in MEDIUM_FIELDS:
        return "medium"
    return "fast"    # default for any unknown field


# ── Life stage multipliers ──────────────────────────────────────────────────
#
# Applied as a divisor to half-life: effective = base / multiplier.
# Higher multiplier = faster decay.
#
# Puppies change fast (weight doubles in weeks).
# Adults are stable.
# Seniors need more health monitoring again.

LIFE_STAGE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "puppy":  {"fast": 2.0, "medium": 1.5, "slow": 1.25},
    "kitten": {"fast": 2.0, "medium": 1.5, "slow": 1.25},
    "junior": {"fast": 1.5, "medium": 1.25, "slow": 1.0},
    "adult":  {"fast": 1.0, "medium": 1.0, "slow": 1.0},
    "senior": {"fast": 1.5, "medium": 1.25, "slow": 1.0},
}


# ── Color thresholds ────────────────────────────────────────────────────────

_GREEN_THRESHOLD: int = 80
_YELLOW_THRESHOLD: int = 50


# ── Internal helpers ─────────────────────────────────────────────────────────

def _normalize_confidence(raw: float | int) -> float:
    """
    Convert confidence to a 0.0-1.0 float.

    Handles both formats found in active_profile.json:
      - Float 0.0-1.0 (Aggregator output)
      - Integer 0-100 (seed data)
    """
    value = float(raw)
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def _compute_decay(
    updated_at_iso: str | None,
    base_half_life: float,
    life_stage: str,
    category: str,
) -> float:
    """
    Compute time-decay factor for a single field.

    Returns a float between 0.3 (floor) and 1.0 (perfectly fresh).
    Static fields always return 1.0.
    Missing/unparseable timestamps return 1.0 (benefit of the doubt for seed data).
    """
    # Static fields never decay.
    if category == "static" or base_half_life <= 0:
        return 1.0

    # No timestamp → treat as fresh (seed data).
    if not updated_at_iso:
        return 1.0

    try:
        updated_at = datetime.fromisoformat(updated_at_iso)
        # Ensure timezone-aware comparison.
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated_at).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 1.0

    if age_days <= 0:
        return 1.0

    # Apply life-stage multiplier to speed up or slow down decay.
    stage_multipliers = LIFE_STAGE_MULTIPLIERS.get(life_stage, LIFE_STAGE_MULTIPLIERS["adult"])
    multiplier = stage_multipliers.get(category, 1.0)

    effective_half_life = base_half_life / multiplier

    # Exponential decay: 0.5 ^ (age / half_life), floored at 0.3.
    decay = math.pow(0.5, age_days / effective_half_life)
    return max(0.3, decay)


# ── Public API ──────────────────────────────────────────────────────────────

def calculate_confidence_score(active_profile: dict, pet_profile: dict) -> int:
    """
    Compute the overall confidence score for a pet.

    Parameters
    ----------
    active_profile : dict
        The merged active profile dict (keys → entry dicts with value, confidence,
        updated_at, etc.).  May contain extra keys (vomiting, limping) that are
        ignored — only the canonical 22 fields are scored.
    pet_profile : dict
        Static pet identity (pet_id, name, species, breed, date_of_birth, sex,
        life_stage).

    Returns
    -------
    int
        Score from 0 to 100 (inclusive).
    """
    life_stage = pet_profile.get("life_stage", "adult")
    numerator = 0.0

    for field, weight in FIELD_WEIGHTS.items():
        entry = active_profile.get(field)
        if not entry or not isinstance(entry, dict):
            # Field is a gap — contributes 0 to numerator.
            continue

        raw_confidence = entry.get("confidence", 0.0)
        confidence = _normalize_confidence(raw_confidence)

        category = _decay_category(field)
        base_half_life = BASE_HALF_LIVES[category]
        updated_at_iso = entry.get("updated_at")

        decay = _compute_decay(updated_at_iso, base_half_life, life_stage, category)

        numerator += confidence * decay * weight

    score = (numerator / TOTAL_WEIGHT) * 100.0
    return max(0, min(100, round(score)))


def confidence_color(score: int) -> str:
    """
    Map a confidence score to a color label.

    Green  (80-100): well-informed
    Yellow (50-79):  some gaps or outdated info
    Red    (0-49):   significant gaps
    """
    if score >= _GREEN_THRESHOLD:
        return "green"
    if score >= _YELLOW_THRESHOLD:
        return "yellow"
    return "red"
