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
