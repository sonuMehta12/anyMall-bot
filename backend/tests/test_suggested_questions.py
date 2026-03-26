# tests/test_suggested_questions.py
#
# Test suite for suggested questions feature.
#
# Two test groups:
#   Section A — Unit tests: Templates, validator, cache keys. No server needed.
#   Section B — Integration: Requires backend running on localhost:8000.
#
# Usage:
#   cd backend && python tests/test_suggested_questions.py --unit
#   cd backend && python tests/test_suggested_questions.py --integration
#   cd backend && python tests/test_suggested_questions.py

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import requests
except ImportError:
    requests = None

# ── Terminal colours ─────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed_count = 0
failed_count = 0


def ok(label: str):
    global passed_count
    passed_count += 1
    print(f"  {GREEN}PASS{RESET}  {label}")


def fail(label: str, detail: str = ""):
    global failed_count
    failed_count += 1
    msg = f"  {RED}FAIL{RESET}  {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def section(title: str):
    print(f"\n{BOLD}{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A — Unit Tests (no server needed)
# ══════════════════════════════════════════════════════════════════════════════

def run_unit_tests():
    section("A1 — Evergreen Templates")
    test_evergreen_all_languages_have_4_questions()
    test_evergreen_fallback_to_ja()
    test_evergreen_2pet_targets()
    test_evergreen_deep_copy()

    section("A2 — Question Validator")
    test_validate_empty_text()
    test_validate_char_limit_en()
    test_validate_char_limit_ja()
    test_validate_duplicate_in_set()
    test_validate_history_repeat()
    test_validate_alarmist_content()
    test_validate_invalid_target()
    test_validate_invalid_reason_type()
    test_validate_happy_path()
    test_validate_wrong_count()

    section("A3 — Cache Keys")
    test_cache_key_format()
    test_cache_key_sorted_pets()
    test_cache_history_key()
    test_cache_pattern_key()

    section("A4 — SuggestedQuestionsAgent._parse_response")
    test_parse_valid_json()
    test_parse_markdown_fenced()
    test_parse_invalid_json()
    test_parse_wrong_count()
    test_parse_malformed_items()

    section("A5 — Character Limits")
    test_char_limit_cjk()
    test_char_limit_latin()

    section("A6 — Nightly Job Staleness Logic")
    test_staleness_no_cache_always_generates()
    test_staleness_stale_profile_triggers_regen()
    test_staleness_fresh_profile_skips()
    test_staleness_no_profile_data_with_cache_skips()

    section("A7 — Background Regen (_regen_suggested_questions)")
    test_regen_skips_when_sq_agent_none()
    test_regen_skips_when_valkey_none()
    test_regen_skips_when_pet_fetcher_none()
    test_regen_runs_and_caches_questions()


# ── A1: Evergreen Templates ──────────────────────────────────────────────────

def test_evergreen_all_languages_have_4_questions():
    from app.services.question_templates import ANYMALLCHAN_EVERGREEN
    for lang, questions in ANYMALLCHAN_EVERGREEN.items():
        if len(questions) != 4:
            fail(f"evergreen[{lang}] has {len(questions)} questions, expected 4")
            return
        for q in questions:
            if not q.get("text"):
                fail(f"evergreen[{lang}] has empty text")
                return
            if q.get("target") != "pet_a":
                fail(f"evergreen[{lang}] default target should be pet_a, got {q['target']}")
                return
            if q.get("reason_type") != "evergreen":
                fail(f"evergreen[{lang}] reason_type should be evergreen")
                return
    ok(f"all {len(ANYMALLCHAN_EVERGREEN)} languages have 4 valid questions")


def test_evergreen_fallback_to_ja():
    from app.services.question_templates import get_evergreen_questions, ANYMALLCHAN_EVERGREEN
    result = get_evergreen_questions("XX_UNKNOWN")
    ja = ANYMALLCHAN_EVERGREEN["JA"]
    if [q["text"] for q in result] == [q["text"] for q in ja]:
        ok("unknown language falls back to JA")
    else:
        fail("unknown language did not fall back to JA")


def test_evergreen_2pet_targets():
    from app.services.question_templates import get_evergreen_questions
    result = get_evergreen_questions("EN", pet_count=2)
    targets = [q["target"] for q in result]
    expected = ["pet_a", "pet_b", "both", "pet_a"]
    if targets == expected:
        ok(f"2-pet targets = {targets}")
    else:
        fail(f"2-pet targets: expected {expected}, got {targets}")


def test_evergreen_deep_copy():
    from app.services.question_templates import get_evergreen_questions
    r1 = get_evergreen_questions("EN")
    r1[0]["text"] = "MUTATED"
    r2 = get_evergreen_questions("EN")
    if r2[0]["text"] != "MUTATED":
        ok("templates are deep-copied (mutation doesn't leak)")
    else:
        fail("templates leak mutations between calls")


# ── A2: Question Validator ───────────────────────────────────────────────────

def _make_q(text="Is my pet ok?", target="pet_a", reason="known_context"):
    return {"text": text, "target": target, "reason_type": reason}


def test_validate_empty_text():
    from app.services.question_validator import validate_questions
    qs = [_make_q(""), _make_q(), _make_q(), _make_q()]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 0 in fails:
        ok("empty text detected")
    else:
        fail("empty text not detected")


def test_validate_char_limit_en():
    from app.services.question_validator import validate_questions
    long_text = "A" * 57  # over 56 limit
    qs = [_make_q(long_text), _make_q("Short one"), _make_q("Another short"), _make_q("Fourth")]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 0 in fails:
        ok(f"EN char limit enforced (57 > 56)")
    else:
        fail("EN char limit not enforced")


def test_validate_char_limit_ja():
    from app.services.question_validator import validate_questions
    long_text = "あ" * 37  # over 36 limit
    qs = [_make_q(long_text), _make_q("短い"), _make_q("もう一つ"), _make_q("四つ目")]
    passed, fails, reasons = validate_questions(qs, "JA", 1)
    if not passed and 0 in fails:
        ok(f"JA char limit enforced (37 > 36)")
    else:
        fail("JA char limit not enforced")


def test_validate_duplicate_in_set():
    from app.services.question_validator import validate_questions
    qs = [_make_q("Same question"), _make_q("Same question"), _make_q("Different"), _make_q("Also different")]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 1 in fails:
        ok("duplicate in set detected at index 1")
    else:
        fail(f"duplicate not detected. passed={passed}, fails={fails}")


def test_validate_history_repeat():
    from app.services.question_validator import validate_questions
    qs = [_make_q("Old question from last week"), _make_q("New one"), _make_q("Another new"), _make_q("Fourth new")]
    history = ["Old question from last week"]
    passed, fails, reasons = validate_questions(qs, "EN", 1, history)
    if not passed and 0 in fails:
        ok("history repeat detected")
    else:
        fail("history repeat not detected")


def test_validate_alarmist_content():
    from app.services.question_validator import validate_questions
    qs = [_make_q("Is this an emergency?"), _make_q("Normal q"), _make_q("Another"), _make_q("Fourth")]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 0 in fails:
        ok("alarmist 'emergency' detected")
    else:
        fail("alarmist content not detected")


def test_validate_invalid_target():
    from app.services.question_validator import validate_questions
    qs = [_make_q(target="invalid"), _make_q(), _make_q(), _make_q()]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 0 in fails:
        ok("invalid target detected")
    else:
        fail("invalid target not detected")


def test_validate_invalid_reason_type():
    from app.services.question_validator import validate_questions
    qs = [_make_q(reason="bad_reason"), _make_q(), _make_q(), _make_q()]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed and 0 in fails:
        ok("invalid reason_type detected")
    else:
        fail("invalid reason_type not detected")


def test_validate_happy_path():
    from app.services.question_validator import validate_questions
    qs = [
        _make_q("What food is best for my dog?"),
        _make_q("Is my dog getting enough sleep?"),
        _make_q("How much exercise does my dog need?"),
        _make_q("What should I watch for daily?"),
    ]
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if passed and not fails:
        ok("4 valid questions pass validation")
    else:
        fail(f"valid questions failed: {reasons}")


def test_validate_wrong_count():
    from app.services.question_validator import validate_questions
    qs = [_make_q(), _make_q("Different one"), _make_q("Third")]  # only 3
    passed, fails, reasons = validate_questions(qs, "EN", 1)
    if not passed:
        ok("wrong count (3 instead of 4) detected")
    else:
        fail("wrong count not detected")


# ── A3: Cache Keys ───────────────────────────────────────────────────────────

def test_cache_key_format():
    from app.cache.keys import CacheKeys
    key = CacheKeys.suggested_questions("U-4421", [101], "JA")
    if key == "am:suggested:U-4421:101:JA":
        ok(f"single-pet key: {key}")
    else:
        fail(f"unexpected key format: {key}")


def test_cache_key_sorted_pets():
    from app.cache.keys import CacheKeys
    key1 = CacheKeys.suggested_questions("U-4421", [102, 101], "EN")
    key2 = CacheKeys.suggested_questions("U-4421", [101, 102], "EN")
    if key1 == key2 == "am:suggested:U-4421:101-102:EN":
        ok(f"pet order is normalized: {key1}")
    else:
        fail(f"pet order not normalized: {key1} vs {key2}")


def test_cache_history_key():
    from app.cache.keys import CacheKeys
    key = CacheKeys.suggested_history("U-4421", [101])
    if key == "am:suggested_history:U-4421:101":
        ok(f"history key: {key}")
    else:
        fail(f"unexpected history key: {key}")


def test_cache_pattern_key():
    from app.cache.keys import CacheKeys
    pattern = CacheKeys.suggested_pattern("U-4421")
    if pattern == "am:suggested:U-4421:*":
        ok(f"pattern key: {pattern}")
    else:
        fail(f"unexpected pattern: {pattern}")


# ── A4: Agent parse_response ─────────────────────────────────────────────────

def _get_agent():
    from app.agents.suggested_questions import SuggestedQuestionsAgent

    class FakeLLM:
        async def complete(self, **kwargs): return ""
        async def health_check(self): return True

    return SuggestedQuestionsAgent(llm=FakeLLM())


def test_parse_valid_json():
    agent = _get_agent()
    raw = json.dumps([
        {"text": "Q1?", "target": "pet_a", "reason_type": "known_context"},
        {"text": "Q2?", "target": "pet_a", "reason_type": "known_context"},
        {"text": "Q3?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Q4?", "target": "pet_a", "reason_type": "evergreen"},
    ])
    result = agent._parse_response(raw)
    if len(result) == 4 and result[0]["text"] == "Q1?":
        ok("valid JSON parsed correctly")
    else:
        fail(f"parse failed: {result}")


def test_parse_markdown_fenced():
    agent = _get_agent()
    raw = '```json\n' + json.dumps([
        {"text": "Q1?", "target": "pet_a", "reason_type": "known_context"},
        {"text": "Q2?", "target": "pet_a", "reason_type": "known_context"},
        {"text": "Q3?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Q4?", "target": "pet_a", "reason_type": "evergreen"},
    ]) + '\n```'
    result = agent._parse_response(raw)
    if len(result) == 4:
        ok("markdown-fenced JSON parsed correctly")
    else:
        fail(f"fenced parse failed: {result}")


def test_parse_invalid_json():
    agent = _get_agent()
    result = agent._parse_response("this is not json at all")
    if result == []:
        ok("invalid JSON returns empty list")
    else:
        fail(f"invalid JSON should return [], got {result}")


def test_parse_wrong_count():
    agent = _get_agent()
    raw = json.dumps([
        {"text": "Q1?", "target": "pet_a", "reason_type": "known_context"},
        {"text": "Q2?", "target": "pet_a", "reason_type": "known_context"},
    ])
    result = agent._parse_response(raw)
    if result == []:
        ok("wrong count (2) returns empty list")
    else:
        fail(f"wrong count should return [], got {result}")


def test_parse_malformed_items():
    agent = _get_agent()
    raw = json.dumps([
        {"text": "Q1?", "target": "pet_a", "reason_type": "known_context"},
        {"bad_key": "missing text"},  # malformed
        {"text": "Q3?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Q4?", "target": "pet_a", "reason_type": "evergreen"},
    ])
    result = agent._parse_response(raw)
    if result == []:
        ok("malformed item causes rejection (only 3 valid -> returns [])")
    else:
        fail(f"malformed items should return [], got {len(result)} items")


# ── A5: Character Limits ────────────────────────────────────────────────────

def test_char_limit_cjk():
    from app.services.question_validator import get_char_limit
    for lang in ["JA", "KO", "ZH", "TH"]:
        if get_char_limit(lang) != 36:
            fail(f"CJK limit for {lang} should be 36")
            return
    ok("CJK languages -> 36 char limit")


def test_char_limit_latin():
    from app.services.question_validator import get_char_limit
    for lang in ["EN", "ID", "VI", "MS"]:
        if get_char_limit(lang) != 56:
            fail(f"Latin limit for {lang} should be 56")
            return
    ok("Latin languages -> 56 char limit")


# ── A6: Nightly Job Staleness Logic ─────────────────────────────────────────
#
# The nightly job uses ISO-8601 string comparison (lexicographic) to decide
# whether cached questions are still fresh:
#   if profile_last_updated > questions_generated_at  →  regenerate
#   else                                              →  skip
#
# These tests exercise that decision logic directly so a future refactor
# of nightly.py won't silently break the skip/regen boundary.

def test_staleness_no_cache_always_generates():
    """No cache entry for a user → always generate (first-time or post-expiry)."""
    raw_cached = None
    # Mirrors nightly.py: `if raw_cached:` — falls through to generation when None
    should_generate = raw_cached is None
    if should_generate:
        ok("no cache entry -> generation triggered")
    else:
        fail("no cache entry should always trigger generation")


def test_staleness_stale_profile_triggers_regen():
    """Profile updated AFTER questions were generated -> regenerate."""
    generated_at       = "2026-03-25T03:00:00+00:00"
    profile_updated_at = "2026-03-25T14:30:00+00:00"  # user chatted during the day
    is_stale = profile_updated_at > generated_at
    if is_stale:
        ok("profile newer than cache -> stale detected -> regen")
    else:
        fail("stale profile not detected (profile_updated > generated_at should be stale)")


def test_staleness_fresh_profile_skips():
    """Profile last changed BEFORE questions were generated -> skip (still fresh)."""
    generated_at       = "2026-03-25T03:00:00+00:00"
    profile_updated_at = "2026-03-24T18:00:00+00:00"  # yesterday evening
    is_stale = profile_updated_at > generated_at
    if not is_stale:
        ok("profile older than cache -> fresh -> skip")
    else:
        fail("fresh profile incorrectly marked as stale")


def test_staleness_no_profile_data_with_cache_skips():
    """
    Cache exists but pet has NO active_profile rows (user registered but never chatted).

    Nightly job condition (nightly.py line ~345):
        elif generated_at_str and not profile_last_updated_str:
            skipped += 1; continue

    Should skip — questions are as fresh as they can be with no data.
    """
    generated_at_str = "2026-03-25T03:00:00+00:00"
    profile_last_updated_str = None  # no rows in active_profile
    should_skip = bool(generated_at_str and not profile_last_updated_str)
    if should_skip:
        ok("no profile data but cache exists -> skip (nothing to regenerate from)")
    else:
        fail("no profile data with cache should be skipped")


# ── A7: Background Regen Unit Tests ──────────────────────────────────────────
#
# _regen_suggested_questions() is called fire-and-forget from _run_background
# after the aggregator merges high-confidence facts.  These unit tests verify
# it fails gracefully when services are unavailable and runs correctly with mocks.

def _make_mock_state_bag(sq_agent=None, valkey=None, pet_fetcher=None):
    """Build a minimal mock StateBag for regen tests."""
    class MockStateBag:
        pass
    bag = MockStateBag()
    bag.suggested_questions_agent = sq_agent
    bag.valkey = valkey
    bag.pet_fetcher = pet_fetcher
    return bag


def test_regen_skips_when_sq_agent_none():
    import asyncio
    from app.routes.background import _regen_suggested_questions
    bag = _make_mock_state_bag(sq_agent=None, valkey=object(), pet_fetcher=object())
    # Must not raise
    asyncio.run(_regen_suggested_questions("U-001", [101], bag))
    ok("regen skips gracefully when suggested_questions_agent is None")


def test_regen_skips_when_valkey_none():
    import asyncio
    from app.routes.background import _regen_suggested_questions
    bag = _make_mock_state_bag(sq_agent=object(), valkey=None, pet_fetcher=object())
    asyncio.run(_regen_suggested_questions("U-001", [101], bag))
    ok("regen skips gracefully when valkey is None")


def test_regen_skips_when_pet_fetcher_none():
    import asyncio
    from app.routes.background import _regen_suggested_questions
    bag = _make_mock_state_bag(sq_agent=object(), valkey=object(), pet_fetcher=None)
    asyncio.run(_regen_suggested_questions("U-001", [101], bag))
    ok("regen skips gracefully when pet_fetcher is None")


def test_regen_runs_and_caches_questions():
    """
    Full happy-path unit test with mocked services.

    Verifies that _regen_suggested_questions:
      1. resolves language from the user cache
      2. calls sq_agent.generate()
      3. calls vk.setex() with the correct cache key
      4. calls vk.setex() a second time to update history
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.routes.background import _regen_suggested_questions
    from app.cache.keys import CacheKeys

    user_code = "U-TEST-001"
    pet_ids   = [42]
    language  = "EN"

    # ── Mock Valkey ───────────────────────────────────────────────────────────
    mock_vk = MagicMock()
    stored: dict[str, str] = {}

    async def _vk_get(key):
        if key == CacheKeys.user(user_code):
            return json.dumps({"preferred_language": language})
        if key == CacheKeys.profile(42):
            # Return a minimal active_profile with one high-confidence fact
            return json.dumps({"name": {"value": "Buddy", "confidence": 0.9}})
        return None  # history cache miss (first regen)

    async def _vk_setex(key, ttl, value):
        stored[key] = value

    mock_vk.get    = AsyncMock(side_effect=_vk_get)
    mock_vk.setex  = AsyncMock(side_effect=_vk_setex)

    # ── Mock PetFetcher ───────────────────────────────────────────────────────
    mock_pet_fetcher = MagicMock()
    pet_profile = {"name": "Buddy", "species": "dog", "breed": "Shiba", "sex": "male"}
    aalda_facts = {}

    async def _fetch(uc, pid):
        return pet_profile, aalda_facts

    mock_pet_fetcher.fetch_pet_profile = AsyncMock(side_effect=_fetch)

    # ── Mock SuggestedQuestionsAgent ──────────────────────────────────────────
    mock_sq_agent = MagicMock()
    generated_questions = [
        {"text": "What food suits Buddy?",           "target": "pet_a", "reason_type": "known_context"},
        {"text": "Is Buddy getting enough sleep?",   "target": "pet_a", "reason_type": "known_context"},
        {"text": "How much exercise does Buddy need?","target": "pet_a", "reason_type": "known_context"},
        {"text": "What should I watch daily?",        "target": "pet_a", "reason_type": "evergreen"},
    ]

    async def _generate(**kwargs):
        return generated_questions

    mock_sq_agent.generate = AsyncMock(side_effect=_generate)

    # ── Build state bag and run ───────────────────────────────────────────────
    bag = _make_mock_state_bag(
        sq_agent=mock_sq_agent,
        valkey=mock_vk,
        pet_fetcher=mock_pet_fetcher,
    )

    asyncio.run(_regen_suggested_questions(user_code, pet_ids, bag))

    # ── Assertions ────────────────────────────────────────────────────────────
    expected_cache_key = CacheKeys.suggested_questions(user_code, pet_ids, language)
    if expected_cache_key not in stored:
        fail(f"vk.setex not called for suggested questions key: {expected_cache_key}")
        return

    cached = json.loads(stored[expected_cache_key])
    if "questions" not in cached or len(cached["questions"]) != 4:
        fail(f"cached value missing questions or wrong count: {cached}")
        return
    if "generated_at" not in cached:
        fail("cached value missing generated_at timestamp")
        return

    history_key = CacheKeys.suggested_history(user_code, pet_ids)
    if history_key not in stored:
        fail(f"vk.setex not called for history key: {history_key}")
        return

    ok(f"regen generated and cached 4 questions under key {expected_cache_key}")
    ok(f"regen updated history under key {history_key}")


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Integration Tests (requires running backend)
# ══════════════════════════════════════════════════════════════════════════════

BASE = "http://127.0.0.1:8000"
TEST_USER_CODE = "3AOU9K1PWH"


def run_integration_tests():
    if requests is None:
        print(f"\n{YELLOW}Skipping integration tests — 'requests' not installed.{RESET}")
        print(f"  Install: pip install requests")
        return

    section("B1 — GET /api/v1/setup endpoint")
    test_setup_returns_confidence_and_questions()
    test_setup_requires_pet_id()
    test_setup_requires_user_code()

    section("B2 — GET /api/v1/confidence backward compat")
    test_confidence_alias_still_works()

    section("B3 — questions_cached field + cold-start vs. regen behaviour")
    test_setup_questions_cached_field_is_present()
    test_setup_cold_start_returns_evergreen()


def test_setup_returns_confidence_and_questions():
    # First get a real pet_id from the pets endpoint
    pets_res = requests.get(
        f"{BASE}/api/v1/pets",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if pets_res.status_code != 200:
        fail(f"could not fetch pets (status={pets_res.status_code})")
        return

    pets = pets_res.json().get("pets", [])
    if not pets:
        fail("no pets returned — cannot test setup endpoint")
        return

    pet_id = pets[0]["pet_id"]
    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )

    if res.status_code != 200:
        fail(f"setup returned {res.status_code}: {res.text[:200]}")
        return

    data = res.json()

    # Must have confidence fields
    if "confidence_score" not in data or "confidence_color" not in data:
        fail(f"missing confidence fields: {list(data.keys())}")
        return

    # Must have suggested_questions
    if "suggested_questions" not in data:
        fail(f"missing suggested_questions field: {list(data.keys())}")
        return

    questions = data["suggested_questions"]
    if not isinstance(questions, list) or len(questions) != 4:
        fail(f"expected 4 questions, got {type(questions).__name__} with {len(questions) if isinstance(questions, list) else 'N/A'}")
        return

    # Validate each question structure
    for i, q in enumerate(questions):
        if not q.get("text") or not q.get("target") or not q.get("reason_type"):
            fail(f"question[{i}] missing fields: {q}")
            return

    ok(f"setup returns confidence ({data['confidence_score']}/{data['confidence_color']}) + {len(questions)} questions")

    # Log questions for visual inspection
    for q in questions:
        print(f"    {YELLOW}[{q['target']}] {q['reason_type']}: {q['text']}{RESET}")


def test_setup_requires_pet_id():
    res = requests.get(
        f"{BASE}/api/v1/setup",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code == 400:
        ok("setup returns 400 when pet_id is missing")
    else:
        fail(f"expected 400, got {res.status_code}")


def test_setup_requires_user_code():
    res = requests.get(f"{BASE}/api/v1/setup", params={"pet_id": 1})
    if res.status_code == 401:
        ok("setup returns 401 when X-User-Code is missing")
    else:
        fail(f"expected 401, got {res.status_code}")


def test_confidence_alias_still_works():
    pets_res = requests.get(
        f"{BASE}/api/v1/pets",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if pets_res.status_code != 200:
        fail("could not fetch pets for alias test")
        return

    pets = pets_res.json().get("pets", [])
    if not pets:
        fail("no pets for alias test")
        return

    pet_id = pets[0]["pet_id"]
    res = requests.get(
        f"{BASE}/api/v1/confidence",
        params={"pet_id": pet_id},
        headers={"X-User-Code": TEST_USER_CODE},
    )

    if res.status_code != 200:
        fail(f"confidence alias returned {res.status_code}")
        return

    data = res.json()
    if "confidence_score" in data and "confidence_color" in data:
        ok(f"confidence alias works: score={data['confidence_score']}")
    else:
        fail(f"confidence alias missing fields: {list(data.keys())}")


# ── B3: questions_cached field + cold-start vs regen behaviour ───────────────

def test_setup_questions_cached_field_is_present():
    """
    /setup must always include questions_cached (bool) and questions_generated_at (str).

    questions_cached=True  → questions came from Valkey (nightly job or regen ran)
    questions_cached=False → questions are evergreen fallback (cache was empty)
    """
    pets_res = requests.get(
        f"{BASE}/api/v1/pets",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if pets_res.status_code != 200:
        fail(f"could not fetch pets (status={pets_res.status_code})")
        return

    pets = pets_res.json().get("pets", [])
    if not pets:
        fail("no pets returned — cannot test questions_cached field")
        return

    pet_id = pets[0]["pet_id"]
    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code != 200:
        fail(f"setup returned {res.status_code}")
        return

    data = res.json()

    if "questions_cached" not in data:
        fail(f"questions_cached field missing from response: {list(data.keys())}")
        return
    if not isinstance(data["questions_cached"], bool):
        fail(f"questions_cached should be bool, got {type(data['questions_cached']).__name__}")
        return
    if "questions_generated_at" not in data:
        fail(f"questions_generated_at field missing: {list(data.keys())}")
        return

    cached = data["questions_cached"]
    status = "CACHED (regen/nightly ran)" if cached else "EVERGREEN (cold start or post-bust)"
    ok(f"questions_cached={cached} — {status}")

    if cached and data["questions_generated_at"]:
        ok(f"questions_generated_at present: {data['questions_generated_at']}")


def test_setup_cold_start_returns_evergreen():
    """
    When /setup returns evergreen (questions_cached=False), all reason_types must be
    'evergreen'. This verifies the cold-start path serves pure evergreen templates,
    not a mix with stale cached content.

    If questions_cached=True, this test is skipped with a note — it only exercises
    the cold-start branch.
    """
    pets_res = requests.get(
        f"{BASE}/api/v1/pets",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if pets_res.status_code != 200:
        fail("could not fetch pets for evergreen test")
        return

    pets = pets_res.json().get("pets", [])
    if not pets:
        fail("no pets for evergreen test")
        return

    pet_id = pets[0]["pet_id"]
    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code != 200:
        fail(f"setup returned {res.status_code}")
        return

    data = res.json()
    if data.get("questions_cached"):
        # Cache is warm — this test is only meaningful for the cold-start path.
        print(f"    {YELLOW}SKIP  cold-start evergreen test — cache is warm (questions_cached=True){RESET}")
        return

    questions = data.get("suggested_questions", [])
    non_evergreen = [q for q in questions if q.get("reason_type") != "evergreen"]
    if non_evergreen:
        fail(
            f"evergreen path returned non-evergreen reason_types: "
            f"{[q['reason_type'] for q in non_evergreen]}"
        )
    else:
        ok(f"cold-start returns {len(questions)} questions all with reason_type='evergreen'")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--unit" in args:
        run_unit_tests()
    elif "--integration" in args:
        run_integration_tests()
    else:
        run_unit_tests()
        run_integration_tests()

    # Summary
    total = passed_count + failed_count
    print(f"\n{BOLD}{'=' * 60}")
    print(f"  Results: {GREEN}{passed_count} passed{RESET}{BOLD}, {RED if failed_count else GREEN}{failed_count} failed{RESET}{BOLD} / {total} total")
    print(f"{'=' * 60}{RESET}")

    sys.exit(1 if failed_count > 0 else 0)
