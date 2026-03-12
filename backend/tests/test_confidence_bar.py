# tests/test_confidence_bar.py
#
# Test suite for confidence bar — both unit tests (calculator math) and
# integration tests (API endpoint, score updates after Aggregator).
#
# Two test groups:
#   Section A — Unit tests:  Pure calculator math, no server needed.
#   Section B — Integration: Requires backend running on localhost:8000.
#
# Usage:
#   # Unit tests only (no server needed):
#   cd backend && python tests/test_confidence_bar.py --unit
#
#   # Integration tests (start backend first):
#   cd backend && python tests/test_confidence_bar.py --integration
#
#   # All tests:
#   cd backend && python tests/test_confidence_bar.py

import sys
import os
import time
import uuid
import copy

# Add backend/ to path so we can import app modules for unit tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import requests
except ImportError:
    requests = None  # only needed for integration tests


# ── Terminal colours ─────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ── Config ───────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"


# ── Helpers ──────────────────────────────────────────────────────────────────

def new_sid() -> str:
    return f"conf-{uuid.uuid4().hex[:10]}"


def post_chat(message: str, session_id: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message, "session_id": session_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_confidence() -> dict:
    """GET /confidence — dedicated endpoint (to be implemented)."""
    resp = requests.get(f"{BASE_URL}/confidence", timeout=10)
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION A — UNIT TESTS (calculator math, no server needed)
# ═══════════════════════════════════════════════════════════════════════════════

from app.services.confidence_calculator import (
    calculate_confidence_score,
    confidence_color,
    TIER_A,
    TIER_B,
    TIER_C,
    TOTAL_WEIGHT,
    FIELD_WEIGHTS,
    _normalize_confidence,
    _compute_decay,
)
from datetime import datetime, timezone, timedelta


def make_entry(value: str, confidence: float, days_ago: int = 0) -> dict:
    """Build an active_profile entry dict with a timestamp N days ago."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "value": value,
        "confidence": confidence,
        "source_rank": "explicit_owner",
        "time_scope": "current",
        "source_quote": value,
        "updated_at": ts,
        "session_id": "test",
        "status": "confirmed",
        "change_detected": "",
        "trend_flag": "",
    }


DEFAULT_PET_PROFILE = {
    "pet_id": "luna-001",
    "name": "Luna",
    "species": "dog",
    "breed": "Shiba Inu",
    "date_of_birth": "2024-01-15",
    "sex": "female",
    "life_stage": "adult",
}


# ── A1. Empty profile → score 0 ─────────────────────────────────────────────

def test_empty_profile_score_zero() -> bool:
    """Empty active_profile should produce score 0."""
    score = calculate_confidence_score({}, DEFAULT_PET_PROFILE)
    assert score == 0, f"Expected 0, got {score}"
    return True


# ── A2. All 22 fields at 1.0 confidence → score 100 ─────────────────────────

def test_all_fields_perfect_score_100() -> bool:
    """All 22 fields with confidence=1.0, fresh timestamps → score 100."""
    profile = {}
    all_fields = TIER_A | TIER_B | TIER_C
    for field in all_fields:
        profile[field] = make_entry("test_value", 1.0, days_ago=0)
    score = calculate_confidence_score(profile, DEFAULT_PET_PROFILE)
    assert score == 100, f"Expected 100, got {score}"
    return True


# ── A3. Only Tier A fields → expected weighted score ─────────────────────────

def test_only_tier_a_fields() -> bool:
    """Only 8 Tier A fields at confidence=1.0 → score = (8*3)/46*100 = 52."""
    profile = {}
    for field in TIER_A:
        profile[field] = make_entry("test_value", 1.0, days_ago=0)
    score = calculate_confidence_score(profile, DEFAULT_PET_PROFILE)
    expected = round((8 * 3 / TOTAL_WEIGHT) * 100)  # 52
    assert score == expected, f"Expected {expected}, got {score}"
    return True


# ── A4. Only Tier B fields → expected weighted score ─────────────────────────

def test_only_tier_b_fields() -> bool:
    """Only 7 Tier B fields at confidence=1.0 → score = (7*2)/46*100 = 30."""
    profile = {}
    for field in TIER_B:
        profile[field] = make_entry("test_value", 1.0, days_ago=0)
    score = calculate_confidence_score(profile, DEFAULT_PET_PROFILE)
    expected = round((7 * 2 / TOTAL_WEIGHT) * 100)  # 30
    assert score == expected, f"Expected {expected}, got {score}"
    return True


# ── A5. Only Tier C fields → expected weighted score ─────────────────────────

def test_only_tier_c_fields() -> bool:
    """Only 8 Tier C fields at confidence=1.0 → score = (8*1)/46*100 = 17."""
    profile = {}
    for field in TIER_C:
        profile[field] = make_entry("test_value", 1.0, days_ago=0)
    score = calculate_confidence_score(profile, DEFAULT_PET_PROFILE)
    expected = round((8 * 1 / TOTAL_WEIGHT) * 100)  # 17
    assert score == expected, f"Expected {expected}, got {score}"
    return True


# ── A6. Normalize confidence: 0-100 integer → 0-1 float ─────────────────────

def test_normalize_confidence_integer() -> bool:
    """Confidence=85 (integer) should normalize to 0.85."""
    result = _normalize_confidence(85)
    assert abs(result - 0.85) < 0.001, f"Expected 0.85, got {result}"
    return True


# ── A7. Normalize confidence: float already in 0-1 ──────────────────────────

def test_normalize_confidence_float() -> bool:
    """Confidence=0.85 (float) stays as 0.85."""
    result = _normalize_confidence(0.85)
    assert abs(result - 0.85) < 0.001, f"Expected 0.85, got {result}"
    return True


# ── A8. Static fields never decay ────────────────────────────────────────────

def test_static_fields_no_decay() -> bool:
    """Static fields (breed, species, sex) should have decay=1.0 even if old."""
    decay = _compute_decay(
        (datetime.now(timezone.utc) - timedelta(days=365)).isoformat(),
        0.0,  # base_half_life=0 for static
        "adult",
        "static",
    )
    assert decay == 1.0, f"Expected 1.0, got {decay}"
    return True


# ── A9. Fast field decays after half-life ────────────────────────────────────

def test_fast_field_decay_after_halflife() -> bool:
    """Fast field (45-day half-life) should be ~0.5 after 45 days for adult."""
    ts = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    decay = _compute_decay(ts, 45.0, "adult", "fast")
    # Should be approximately 0.5 (exponential decay)
    assert 0.45 <= decay <= 0.55, f"Expected ~0.5, got {decay}"
    return True


# ── A10. Decay floor at 0.3 ─────────────────────────────────────────────────

def test_decay_floor() -> bool:
    """Even very old fields should not decay below 0.3."""
    ts = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    decay = _compute_decay(ts, 45.0, "adult", "fast")
    assert decay == 0.3, f"Expected 0.3 (floor), got {decay}"
    return True


# ── A11. Puppy multiplier speeds up decay ───────────────────────────────────

def test_puppy_faster_decay() -> bool:
    """Puppy life stage should decay faster than adult for fast fields."""
    ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    adult_decay = _compute_decay(ts, 45.0, "adult", "fast")
    puppy_decay = _compute_decay(ts, 45.0, "puppy", "fast")
    assert puppy_decay < adult_decay, (
        f"Puppy decay ({puppy_decay}) should be less than adult ({adult_decay})"
    )
    return True


# ── A12. Non-scored fields (vomiting, limping) are ignored ───────────────────

def test_non_scored_fields_ignored() -> bool:
    """Fields not in the 22 scored set should not affect the score."""
    profile_a = {}
    profile_b = {
        "vomiting": make_entry("yes", 1.0),
        "limping": make_entry("badly", 1.0),
        "crying": make_entry("yes", 1.0),
        "seizure": make_entry("active", 1.0),
    }
    score_a = calculate_confidence_score(profile_a, DEFAULT_PET_PROFILE)
    score_b = calculate_confidence_score(profile_b, DEFAULT_PET_PROFILE)
    assert score_a == score_b, f"Non-scored fields changed score: {score_a} vs {score_b}"
    return True


# ── A13. Color thresholds ────────────────────────────────────────────────────

def test_confidence_color_thresholds() -> bool:
    """Verify color mapping: green >= 80, yellow >= 50, red < 50."""
    assert confidence_color(100) == "green"
    assert confidence_color(80) == "green"
    assert confidence_color(79) == "yellow"
    assert confidence_color(50) == "yellow"
    assert confidence_color(49) == "red"
    assert confidence_color(0) == "red"
    return True


# ── A14. Partial confidence lowers contribution ──────────────────────────────

def test_partial_confidence_lowers_score() -> bool:
    """A field with confidence=0.5 contributes half vs confidence=1.0."""
    profile_full = {"weight": make_entry("5kg", 1.0)}
    profile_half = {"weight": make_entry("5kg", 0.5)}
    score_full = calculate_confidence_score(profile_full, DEFAULT_PET_PROFILE)
    score_half = calculate_confidence_score(profile_half, DEFAULT_PET_PROFILE)
    assert score_full > score_half, (
        f"Full confidence ({score_full}) should be > half ({score_half})"
    )
    return True


# ── A15. Verify current active_profile produces ~73% ────────────────────────

def test_current_data_approximately_73() -> bool:
    """
    Reproduce the 73% score from the actual active_profile data.

    Present scored fields (from active_profile.json):
      Tier A (8 fields, weight 3): species*, breed*, age, weight, diet_type,
             medications, chronic_illness, allergies = 8/8 present
      Tier B (7 fields, weight 2): sex*, neutered_spayed, energy_level, appetite,
             past_conditions = 5/7 (missing: vaccinations, food_brand)
      Tier C (8 fields, weight 1): temperament, behavioral_traits, activity_level
             = 3/8 (missing: vet_name, last_vet_visit, microchipped, insurance, past_medications)

    * = from pet_profile static merge
    """
    profile = {
        # Tier A — all 8 present
        "species": make_entry("dog", 1.0),
        "breed": make_entry("Shiba Inu", 1.0),
        "age": make_entry("2 years", 0.85),
        "weight": make_entry("4.5 kg", 0.95),
        "diet_type": make_entry("wet food only", 0.85),
        "medications": make_entry("antibiotics", 0.90),  # 90 as integer
        "chronic_illness": make_entry("none confirmed", 1.0),
        "allergies": make_entry("shellfish", 0.85),
        # Tier B — 5/7 present (missing vaccinations, food_brand)
        "sex": make_entry("female", 1.0),
        "neutered_spayed": make_entry("spayed", 1.0),
        "energy_level": make_entry("moderate", 0.9),
        "appetite": make_entry("hasn't been eating well", 0.85),
        "past_conditions": make_entry("no flea issues", 0.85),
        # Tier C — 3/8 present
        "temperament": make_entry("happy when fed", 0.85),
        "behavioral_traits": make_entry("loves walks", 0.85),
        "activity_level": make_entry("lies around", 0.85),
    }
    score = calculate_confidence_score(profile, DEFAULT_PET_PROFILE)
    # With fresh timestamps (no decay), the numerator is:
    # Tier A: (1.0 + 1.0 + 0.85 + 0.95 + 0.85 + 0.90 + 1.0 + 0.85) * 3 = 7.4 * 3 = 22.2
    # Tier B: (1.0 + 1.0 + 0.9 + 0.85 + 0.85) * 2 = 4.6 * 2 = 9.2
    # Tier C: (0.85 + 0.85 + 0.85) * 1 = 2.55
    # Total = 33.95 / 46 * 100 = 73.8 → rounds to 74
    # With real timestamps having some decay, it's around 73
    assert 70 <= score <= 77, f"Expected ~73, got {score}"
    return True


# ── A16. Adding missing fields should increase score ─────────────────────────

def test_adding_fields_increases_score() -> bool:
    """Adding vaccinations (Tier B, weight 2) to partial profile increases score."""
    base = {
        "species": make_entry("dog", 1.0),
        "breed": make_entry("Shiba Inu", 1.0),
    }
    with_vacc = copy.deepcopy(base)
    with_vacc["vaccinations"] = make_entry("up to date", 0.9)

    score_base = calculate_confidence_score(base, DEFAULT_PET_PROFILE)
    score_with = calculate_confidence_score(with_vacc, DEFAULT_PET_PROFILE)
    assert score_with > score_base, (
        f"Adding vaccinations should increase score: {score_base} → {score_with}"
    )
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION B — INTEGRATION TESTS (require backend on localhost:8000)
# ═══════════════════════════════════════════════════════════════════════════════

# ── B1. GET /confidence returns valid response ───────────────────────────────

def test_confidence_endpoint_exists() -> bool:
    """GET /confidence returns 200 with score and color fields."""
    data = get_confidence()
    assert "confidence_score" in data, f"Missing confidence_score: {data}"
    assert "confidence_color" in data, f"Missing confidence_color: {data}"
    score = data["confidence_score"]
    color = data["confidence_color"]
    assert isinstance(score, int) and 0 <= score <= 100, f"Bad score: {score}"
    assert color in ("green", "yellow", "red"), f"Bad color: {color}"
    return True


# ── B2. GET /confidence score matches POST /chat score ───────────────────────

def test_confidence_matches_chat_response() -> bool:
    """Dedicated endpoint should return same score as chat response."""
    sid = new_sid()
    chat_data = post_chat("How is Luna doing today?", sid)
    endpoint_data = get_confidence()
    # They read from the same data, so should be identical (or very close
    # if Aggregator wrote between the two calls)
    chat_score = chat_data["confidence_score"]
    endpoint_score = endpoint_data["confidence_score"]
    diff = abs(chat_score - endpoint_score)
    assert diff <= 5, (
        f"Score mismatch: chat={chat_score}, endpoint={endpoint_score}, diff={diff}"
    )
    return True


# ── B3. Chat response always includes confidence fields ──────────────────────

def test_chat_response_has_confidence() -> bool:
    """POST /chat response must always include confidence_score and confidence_color."""
    sid = new_sid()
    data = post_chat("Luna is happy today", sid)
    assert "confidence_score" in data, f"Missing confidence_score in response"
    assert "confidence_color" in data, f"Missing confidence_color in response"
    assert isinstance(data["confidence_score"], int)
    assert data["confidence_color"] in ("green", "yellow", "red")
    return True


# ── B4. Score reflects Aggregator update (no 1-turn lag) ────────────────────

def test_score_updates_after_aggregator() -> bool:
    """
    After sending a message with extractable facts, the confidence score
    should reflect the Aggregator's update WITHOUT requiring another /chat call.

    This tests the fix for the 1-turn lag bug.
    """
    sid = new_sid()

    # Get baseline score
    baseline = get_confidence()
    baseline_score = baseline["confidence_score"]

    # Send a message with a new fact about a likely-missing field
    post_chat("Luna's vaccinations are fully up to date as of last month", sid)

    # Wait for background pipeline (Compressor + Aggregator)
    time.sleep(3)

    # Fetch fresh score — should reflect the new fact
    after = get_confidence()
    after_score = after["confidence_score"]

    # Score should be >= baseline (new fact added)
    # We can't guarantee exact increase since the field might already exist,
    # but at minimum the score should not decrease
    assert after_score >= baseline_score, (
        f"Score should not decrease after adding facts: {baseline_score} → {after_score}"
    )
    return True


# ── B5. Score is non-zero on initial load (before any chat) ─────────────────

def test_initial_score_nonzero() -> bool:
    """
    GET /confidence should return a non-zero score even before any chat messages.
    Seed data (diet_type, medications, energy_level, etc.) should contribute.
    """
    data = get_confidence()
    score = data["confidence_score"]
    # Seed data has at least diet_type, medications, energy_level,
    # neutered_spayed, chronic_illness → score should be > 0
    assert score > 0, f"Initial score should be non-zero, got {score}"
    return True


# ── B6. Confidence color matches score tier ──────────────────────────────────

def test_color_matches_score() -> bool:
    """Confidence color from API must match the score tier."""
    data = get_confidence()
    score = data["confidence_score"]
    color = data["confidence_color"]

    if score >= 80:
        expected = "green"
    elif score >= 50:
        expected = "yellow"
    else:
        expected = "red"

    assert color == expected, f"Score {score} should be {expected}, got {color}"
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

UNIT_TESTS = [
    ("A1: Empty profile → score 0",                  test_empty_profile_score_zero),
    ("A2: All 22 fields perfect → score 100",         test_all_fields_perfect_score_100),
    ("A3: Only Tier A → 52%",                         test_only_tier_a_fields),
    ("A4: Only Tier B → 30%",                         test_only_tier_b_fields),
    ("A5: Only Tier C → 17%",                         test_only_tier_c_fields),
    ("A6: Normalize integer confidence (85 → 0.85)",  test_normalize_confidence_integer),
    ("A7: Normalize float confidence (0.85 → 0.85)",  test_normalize_confidence_float),
    ("A8: Static fields never decay",                 test_static_fields_no_decay),
    ("A9: Fast field decays after half-life",         test_fast_field_decay_after_halflife),
    ("A10: Decay floor at 0.3",                       test_decay_floor),
    ("A11: Puppy decays faster than adult",           test_puppy_faster_decay),
    ("A12: Non-scored fields (vomiting etc) ignored", test_non_scored_fields_ignored),
    ("A13: Color thresholds (green/yellow/red)",      test_confidence_color_thresholds),
    ("A14: Partial confidence lowers score",          test_partial_confidence_lowers_score),
    ("A15: Current data ≈ 73%",                       test_current_data_approximately_73),
    ("A16: Adding field increases score",             test_adding_fields_increases_score),
]

INTEGRATION_TESTS = [
    ("B1: GET /confidence endpoint exists",           test_confidence_endpoint_exists),
    ("B2: Endpoint score ≈ chat score",               test_confidence_matches_chat_response),
    ("B3: Chat response has confidence fields",       test_chat_response_has_confidence),
    ("B4: Score updates after Aggregator (no lag)",   test_score_updates_after_aggregator),
    ("B5: Initial score is non-zero",                 test_initial_score_nonzero),
    ("B6: Color matches score tier",                  test_color_matches_score),
]


def run_tests(tests: list[tuple[str, callable]], label: str) -> tuple[int, int]:
    """Run a list of tests, print results, return (passed, total)."""
    print(f"\n{BOLD}{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}{RESET}\n")

    passed = 0
    total = len(tests)

    for name, fn in tests:
        try:
            result = fn()
            if result:
                print(f"  {GREEN}PASS{RESET}  {name}")
                passed += 1
            else:
                print(f"  {RED}FAIL{RESET}  {name} (returned False)")
        except AssertionError as e:
            print(f"  {RED}FAIL{RESET}  {name}")
            print(f"         {e}")
        except Exception as e:
            print(f"  {RED}ERROR{RESET} {name}")
            print(f"         {type(e).__name__}: {e}")

    return passed, total


def main():
    args = sys.argv[1:]
    run_unit = "--unit" in args or not args or "--all" in args
    run_integration = "--integration" in args or not args or "--all" in args

    total_passed = 0
    total_tests = 0

    if run_unit:
        p, t = run_tests(UNIT_TESTS, "Section A: Unit Tests (calculator math)")
        total_passed += p
        total_tests += t

    if run_integration:
        if requests is None:
            print(f"\n{RED}Skipping integration tests: 'requests' not installed{RESET}")
        else:
            p, t = run_tests(INTEGRATION_TESTS, "Section B: Integration Tests (API)")
            total_passed += p
            total_tests += t

    # Summary
    print(f"\n{BOLD}{'─' * 60}")
    failed = total_tests - total_passed
    if failed == 0:
        print(f"  {GREEN}All {total_passed}/{total_tests} tests passed!{RESET}")
    else:
        print(f"  {RED}{failed}/{total_tests} tests failed{RESET}")
    print(f"{'─' * 60}{RESET}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
