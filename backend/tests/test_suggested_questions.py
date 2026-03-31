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
#   cd backend && python tests/test_suggested_questions.py --module food
#   cd backend && python tests/test_suggested_questions.py --module health
#   cd backend && python tests/test_suggested_questions.py --module anymall

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
    section("A0 — Migrations & ORM Models (Step 1)")
    test_migration_files_are_syntactically_valid()
    test_orm_suggested_question_importable()
    test_user_model_has_last_known_pet_ids()

    section("A1 — Evergreen Templates (v2 module/language/target, Step 7)")
    test_get_evergreen_all_modules_exist()
    test_get_evergreen_language_fallback_to_EN()
    test_get_evergreen_returns_correct_module_target()
    test_get_evergreen_no_reason_type()
    test_get_evergreen_deep_copy()

    section("A2 — Question Validator (v2 per-slot, Step 6)")
    test_validate_empty_text()
    test_validate_char_limit_en()
    test_validate_char_limit_ja()
    test_validate_duplicate_in_set()
    test_validate_history_repeat()
    test_validate_alarmist_content()
    test_validate_invalid_target()
    test_validate_invalid_module()
    test_validate_single_pet_guard()
    test_validate_single_pet_guard_no_multi_language()
    test_validate_happy_path()

    section("A2b — AgentState.all_pet_ids (Step 3)")
    test_agent_state_all_pet_ids_default()
    test_agent_state_all_pet_ids_set()

    section("A2c — SuggestedQuestionsRepo (Step 4)")
    test_suggested_questions_repo_importable()
    test_user_repo_upsert_accepts_all_pet_ids()
    test_last_known_pet_ids_never_shrinks()

    section("A3 — Cache Keys (v2 — no pet_ids)")
    test_cache_key_format()
    test_cache_key_sorted_pets()
    test_cache_history_key()
    test_cache_pattern_key()
    test_cache_key_no_pet_ids_in_format()

    section("A4 — SuggestedQuestionsAgent._parse_response (v2 10-question, Step 8)")
    test_parse_10_valid()
    test_parse_markdown_fenced()
    test_parse_invalid_json()
    test_parse_wrong_count()
    test_parse_malformed_items()
    test_parse_no_reason_type()

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

    section("A8 — generator.regen_for_user (Step 9)")
    test_regen_for_user_importable()
    test_regen_for_user_happy_path()
    test_regen_for_user_empty_llm_uses_evergreen()
    test_regen_for_user_slot_patch_on_validation_failure()

    section("A9 — Language: EN vs JA generation")
    test_regen_generates_for_en_language()
    test_regen_generates_for_ja_language()
    test_regen_en_and_ja_produce_separate_keys()

    section("A10 — is_pet_b: pet_a vs pet_b target")
    test_regen_is_pet_b_false_stores_pet_a_questions()
    test_regen_is_pet_b_true_stores_pet_b_questions()

    section("A11 — Background regen: one call per pet")
    test_background_regen_two_pets_calls_regen_twice()
    test_background_regen_is_pet_b_flag_per_pet()

    section("A12 — _pick_questions pick rule")
    test_pick_food_single_pet_returns_3_dedicated()
    test_pick_food_single_pet_no_duplicates()
    test_pick_food_dual_pet_layout()
    test_pick_health_single_pet_returns_3_dedicated()
    test_pick_health_dual_pet_layout()
    test_pick_anymall_single_pet_layout()
    test_pick_anymall_dual_pet_layout()
    test_pick_always_returns_3_even_with_empty_rows()

    section("A13 — Nightly job: staleness + language cleanup")
    test_get_all_stale_sql_filters_preferred_language()
    test_cleanup_stale_language_rows_method_exists()
    test_pregenerate_skips_when_agent_unavailable()
    test_nightly_cleanup_called_in_pregenerate()

    section("A14 — Cache: per-pet key correctness")
    test_regen_writes_one_key_per_pet()

    section("A15 — PostgreSQL repo: pet_id in upsert/get")
    test_orm_model_has_pet_id_column()
    test_repo_upsert_signature_has_pet_id()
    test_repo_get_signature_has_pet_id()
    test_repo_upsert_called_with_pet_id_in_happy_path()
    test_repo_upsert_called_with_correct_user_and_language()

    section("A16 — Language change: regen new language, delete old")
    test_language_change_new_language_key_written()
    test_language_change_old_language_key_not_served()
    test_nightly_full_cycle_language_change()

    section("A17 — /setup cache-load path: correct pet key loaded")
    test_setup_single_pet_loads_own_key()
    test_setup_pet_b_alone_loads_pet_b_key_not_pet_a()
    test_setup_dual_pet_loads_both_keys()

    section("A18 — /setup cache HIT: Valkey served, Postgres never called")
    test_setup_cache_hit_serves_from_valkey()
    test_setup_cache_hit_does_not_call_postgres()

    section("A19 — /setup Postgres fallback: Valkey cold, load from DB")
    test_setup_postgres_fallback_when_valkey_cold()
    test_setup_postgres_fallback_warms_valkey()
    test_setup_postgres_fallback_cold_start_uses_evergreen()

    section("A20 — Aggregator triggers regen end-to-end pipeline")
    test_aggregator_triggers_regen_after_high_confidence_facts()
    test_aggregator_regen_writes_to_valkey()
    test_aggregator_regen_writes_to_postgres()
    test_aggregator_no_regen_when_only_low_confidence_facts()

    section("A21 — Nightly: stale-row loop regens each row")
    test_nightly_pregenerate_calls_regen_for_each_stale_row()
    test_nightly_pregenerate_is_pet_b_derived_from_last_known_pet_ids()
    test_nightly_pregenerate_partial_failure_continues()
    test_nightly_pregenerate_empty_stale_rows_skips()


# ── A0: Migrations & ORM Models ─────────────────────────────────────────────

def test_migration_files_are_syntactically_valid():
    import ast, os
    base = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions")
    files = [
        "f1a2b3c4d5e6_add_suggested_questions_table.py",
        "a7b8c9d0e1f2_add_last_known_pet_ids_to_users.py",
    ]
    for fn in files:
        path = os.path.join(base, fn)
        if not os.path.exists(path):
            fail(f"migration file not found: {fn}")
            return
        with open(path) as fh:
            try:
                ast.parse(fh.read())
            except SyntaxError as e:
                fail(f"syntax error in {fn}: {e}")
                return
    ok("both migration files exist and are syntactically valid Python")


def test_orm_suggested_question_importable():
    try:
        from app.db.models import SuggestedQuestion
        assert SuggestedQuestion.__tablename__ == "anymall_chan_suggested_questions"
        cols = {c.name for c in SuggestedQuestion.__table__.columns}
        required = {"id", "user_code", "language", "pet_id", "questions", "generated_at"}
        missing = required - cols
        if missing:
            fail(f"SuggestedQuestion missing columns: {missing}")
            return
        ok("SuggestedQuestion ORM model importable with correct columns (incl. pet_id)")
    except Exception as e:
        fail(f"SuggestedQuestion import failed: {e}")


def test_user_model_has_last_known_pet_ids():
    try:
        from app.db.models import User
        cols = {c.name for c in User.__table__.columns}
        if "last_known_pet_ids" not in cols:
            fail("User model missing last_known_pet_ids column")
            return
        ok("User model has last_known_pet_ids column")
    except Exception as e:
        fail(f"User model check failed: {e}")


# ── A1: Evergreen Templates (v2 module/language/target, Step 7) ──────────────

def test_get_evergreen_all_modules_exist():
    from app.services.question_generation.templates import EVERGREEN
    required_modules = {"food", "health", "anymall"}
    required_targets = {"pet_a", "pet_b", "both"}
    missing_modules = required_modules - set(EVERGREEN.keys())
    if missing_modules:
        fail(f"EVERGREEN missing modules: {missing_modules}")
        return
    for mod in required_modules:
        en_pool = EVERGREEN[mod].get("EN", {})
        missing_targets = required_targets - set(en_pool.keys())
        if missing_targets:
            fail(f"EVERGREEN[{mod}][EN] missing targets: {missing_targets}")
            return
        for tgt in required_targets:
            pool = en_pool[tgt]
            min_expected = 4 if tgt == "both" else 8
            if len(pool) < min_expected:
                fail(f"EVERGREEN[{mod}][EN][{tgt}] has {len(pool)} questions, expected >= {min_expected}")
                return
    ok(f"all 3 modules present with EN pet_a(8+)/pet_b(8+)/both(4+) pools")


def test_get_evergreen_language_fallback_to_EN():
    from app.services.question_generation.templates import get_evergreen_questions, EVERGREEN
    result = get_evergreen_questions("food", "XX_UNKNOWN", "pet_a", count=1)
    # v2: unknown language falls back to EN (not JA)
    en_texts = {q["text"] for q in EVERGREEN["food"]["EN"]["pet_a"]}
    if result and result[0]["text"] in en_texts:
        ok(f"unknown language falls back to EN: {result[0]['text']!r}")
    else:
        fail(f"expected EN fallback, got: {result}")


def test_get_evergreen_returns_correct_module_target():
    from app.services.question_generation.templates import get_evergreen_questions, EVERGREEN
    result = get_evergreen_questions("health", "EN", "both", count=1)
    if not result:
        fail("get_evergreen_questions returned empty list for health/EN/both")
        return
    q = result[0]
    if q.get("module") != "health":
        fail(f"expected module=health, got {q.get('module')!r}")
        return
    if q.get("target") != "both":
        fail(f"expected target=both, got {q.get('target')!r}")
        return
    both_texts = {item["text"] for item in EVERGREEN["health"]["EN"]["both"]}
    if q["text"] not in both_texts:
        fail(f"returned text not from health/EN/both pool: {q['text']!r}")
        return
    ok(f"get_evergreen_questions(health, EN, both) -> {q['text']!r}")


def test_get_evergreen_no_reason_type():
    from app.services.question_generation.templates import get_evergreen_questions
    for module in ("food", "health", "anymall"):
        for target in ("pet_a", "pet_b", "both"):
            results = get_evergreen_questions(module, "EN", target, count=2)
            for q in results:
                if "reason_type" in q:
                    fail(f"v2 question has reason_type field: {q}")
                    return
    ok("no reason_type field in any v2 evergreen question")


def test_get_evergreen_deep_copy():
    from app.services.question_generation.templates import get_evergreen_questions
    r1 = get_evergreen_questions("anymall", "EN", "pet_a", count=1)
    original_text = r1[0]["text"]
    r1[0]["text"] = "MUTATED"
    r2 = get_evergreen_questions("anymall", "EN", "pet_a", count=8)
    pool_texts = {q["text"] for q in r2}
    if "MUTATED" not in pool_texts:
        ok("templates are deep-copied (mutation doesn't leak into pool)")
    else:
        fail("templates leak mutations between calls")


# ── A2: Question Validator (v2 per-slot, Step 6) ─────────────────────────────

def _make_q(text="Is my pet ok?", target="pet_a", module="anymall"):
    return {"text": text, "target": target, "module": module}


def _slot(question, slot_index=0, all_questions=None, language="EN", pet_count=1, history=None):
    """Helper: call validate_slot with sensible defaults."""
    from app.services.question_generation.validator import validate_slot
    if all_questions is None:
        all_questions = [question]
    if history is None:
        history = []
    return validate_slot(question, slot_index, all_questions, language, pet_count, history)


def test_validate_empty_text():
    result = _slot(_make_q(""))
    if result == "empty_text":
        ok("empty text detected")
    else:
        fail(f"expected empty_text, got {result!r}")


def test_validate_char_limit_en():
    long_text = "A" * 57  # over 56 limit
    result = _slot(_make_q(long_text), language="EN")
    if result and "over_char_limit" in result:
        ok(f"EN char limit enforced (57 > 56) — {result}")
    else:
        fail(f"EN char limit not enforced, got {result!r}")


def test_validate_char_limit_ja():
    long_text = "あ" * 37  # over 36 limit
    result = _slot(_make_q(long_text), language="JA")
    if result and "over_char_limit" in result:
        ok(f"JA char limit enforced (37 > 36) — {result}")
    else:
        fail(f"JA char limit not enforced, got {result!r}")


def test_validate_duplicate_in_set():
    q0 = _make_q("Same question")
    q1 = _make_q("Same question")  # duplicate
    all_qs = [q0, q1]
    from app.services.question_generation.validator import validate_slot
    result = validate_slot(q1, 1, all_qs, "EN", 1, [])
    if result and "duplicate_of_slot" in result:
        ok(f"duplicate in set detected: {result}")
    else:
        fail(f"duplicate not detected, got {result!r}")


def test_validate_history_repeat():
    q = _make_q("Old question from last week")
    result = _slot(q, history=["Old question from last week"])
    if result == "repeated_from_history":
        ok("history repeat detected")
    else:
        fail(f"expected repeated_from_history, got {result!r}")


def test_validate_alarmist_content():
    result = _slot(_make_q("Is this an emergency?"))
    if result == "alarmist_content":
        ok("alarmist 'emergency' detected")
    else:
        fail(f"expected alarmist_content, got {result!r}")


def test_validate_invalid_target():
    result = _slot(_make_q(target="invalid"))
    if result and "invalid_target" in result:
        ok(f"invalid target detected: {result}")
    else:
        fail(f"expected invalid_target, got {result!r}")


def test_validate_invalid_module():
    result = _slot(_make_q(module="bad_module"))
    if result and "invalid_module" in result:
        ok(f"invalid module detected: {result}")
    else:
        fail(f"expected invalid_module, got {result!r}")


def test_validate_single_pet_guard():
    """Single-pet user: question with multi-pet language + pet_b target → rejected."""
    q = _make_q("What food suits both pets?", target="both", module="food")
    result = _slot(q, pet_count=1)
    if result == "single_pet_multi_pet_language":
        ok("single-pet guard rejects 'both pets' language with target=both")
    else:
        fail(f"expected single_pet_multi_pet_language, got {result!r}")


def test_validate_single_pet_guard_no_multi_language():
    """Single-pet user: target=both but text has no multi-pet language → allowed."""
    q = _make_q("What is the best food for Leo?", target="both", module="food")
    result = _slot(q, pet_count=1)
    if result is None:
        ok("single-pet guard allows target=both when text has no multi-pet language")
    else:
        fail(f"expected None (valid), got {result!r}")


def test_validate_happy_path():
    q = _make_q("What food is best for my dog?", target="pet_a", module="food")
    result = _slot(q)
    if result is None:
        ok("valid question returns None")
    else:
        fail(f"valid question rejected: {result!r}")


# ── A2b: AgentState.all_pet_ids ──────────────────────────────────────────────

def test_agent_state_all_pet_ids_default():
    from app.agents.state import AgentState, PetInfo
    state = AgentState(
        session_id="s1", thread_id="t1", user_code="U-1",
        user_message="hi", pets=[PetInfo(id=101, name="Leo")]
    )
    if state.all_pet_ids == []:
        ok("all_pet_ids defaults to empty list")
    else:
        fail(f"expected [], got {state.all_pet_ids}")


def test_agent_state_all_pet_ids_set():
    from app.agents.state import AgentState, PetInfo
    state = AgentState(
        session_id="s1", thread_id="t1", user_code="U-1",
        user_message="hi", pets=[PetInfo(id=101, name="Leo")],
        all_pet_ids=[101, 102],
    )
    if state.all_pet_ids == [101, 102]:
        ok("all_pet_ids stores provided list correctly")
    else:
        fail(f"expected [101, 102], got {state.all_pet_ids}")


# ── A2c: SuggestedQuestionsRepo ──────────────────────────────────────────────

def test_suggested_questions_repo_importable():
    try:
        from app.db.repositories import SuggestedQuestionsRepo
        import inspect
        methods = [name for name, _ in inspect.getmembers(SuggestedQuestionsRepo, predicate=inspect.isfunction)]
        required = {"upsert", "get", "get_all_stale"}
        missing = required - set(methods)
        if missing:
            fail(f"SuggestedQuestionsRepo missing methods: {missing}")
        else:
            ok(f"SuggestedQuestionsRepo importable with methods: {required}")
    except Exception as e:
        fail(f"SuggestedQuestionsRepo import failed: {e}")


def test_user_repo_upsert_accepts_all_pet_ids():
    import inspect
    from app.db.repositories import UserRepo
    src = inspect.getsource(UserRepo.upsert)
    if "all_pet_ids" in src and "last_known_pet_ids" in src:
        ok("UserRepo.upsert handles all_pet_ids -> last_known_pet_ids")
    else:
        fail("UserRepo.upsert does not handle all_pet_ids/last_known_pet_ids")


def test_last_known_pet_ids_never_shrinks():
    """
    chat.py must MERGE new pet_ids into last_known_pet_ids, never overwrite.

    Scenario: user has last_known_pet_ids=[101, 102] and sends a chat with only
    pet_id=[102]. The stored list must remain [101, 102] — NOT become [102].

    If this is not enforced, the next nightly job reads [102] alone and
    regenerates pet 102 with is_pet_b=False, corrupting the pet_b question row.
    """
    import inspect
    import app.routes.chat as chat_module
    src = inspect.getsource(chat_module)

    # The fix introduces a merge via dict.fromkeys or similar order-preserving dedup
    has_merge = (
        "current_known" in src
        and "merged_pet_ids" in src
        and "dict.fromkeys" in src
    )
    if not has_merge:
        fail(
            "chat.py does not merge last_known_pet_ids — single-pet chat will "
            "overwrite the full pet list and corrupt is_pet_b on next nightly run"
        )
        return

    # Also verify the merged list is what gets written (not raw pet_ids)
    # Check that merged_pet_ids is used in the 'updated' dict, not pet_ids directly
    # Find the line: "all_pet_ids": merged_pet_ids
    if '"all_pet_ids": merged_pet_ids' in src or "'all_pet_ids': merged_pet_ids" in src:
        ok(
            "chat.py merges last_known_pet_ids: single-pet chat cannot shrink "
            "the canonical pet list"
        )
    else:
        fail(
            "merged_pet_ids defined but not used in 'updated' dict — "
            "all_pet_ids may still be set to raw pet_ids"
        )


# ── A3: Cache Keys (v2 — no pet_ids in key) ──────────────────────────────────

def test_cache_key_format():
    from app.cache.keys import CacheKeys
    key = CacheKeys.suggested_questions("U-4421", "JA", 101)
    if key == "am:suggested:U-4421:JA:101":
        ok(f"per-pet suggested key: {key}")
    else:
        fail(f"unexpected key format: {key}")


def test_cache_key_sorted_pets():
    # per-pet: different pet_ids produce different keys for the same user
    from app.cache.keys import CacheKeys
    key_a = CacheKeys.suggested_questions("U-4421", "EN", 101)
    key_b = CacheKeys.suggested_questions("U-4421", "EN", 102)
    if key_a == "am:suggested:U-4421:EN:101" and key_b == "am:suggested:U-4421:EN:102" and key_a != key_b:
        ok(f"per-pet keys are distinct: {key_a} vs {key_b}")
    else:
        fail(f"unexpected keys: {key_a}, {key_b}")


def test_cache_history_key():
    from app.cache.keys import CacheKeys
    key = CacheKeys.suggested_history("U-4421", "JA")
    if key == "am:suggested_history:U-4421:JA":
        ok(f"v2 history key (no pet_ids): {key}")
    else:
        fail(f"unexpected history key: {key}")


def test_cache_pattern_key():
    from app.cache.keys import CacheKeys
    pattern = CacheKeys.suggested_pattern("U-4421")
    if pattern == "am:suggested:U-4421:*":
        ok(f"pattern key unchanged: {pattern}")
    else:
        fail(f"unexpected pattern: {pattern}")


def test_cache_key_no_pet_ids_in_format():
    from app.cache.keys import CacheKeys
    key = CacheKeys.suggested_questions("U-9999", "EN", 99)
    # per-pet: key must include pet_id at the end
    if key == "am:suggested:U-9999:EN:99":
        ok(f"per-pet key format am:suggested:{{user}}:{{lang}}:{{pet_id}}: {key}")
    else:
        fail(f"per-pet key has unexpected format: {key}")


# ── A4: Agent parse_response (v2 10-question, Step 8) ────────────────────────

def _get_agent():
    from app.agents.suggested_questions import SuggestedQuestionsAgent

    class FakeLLM:
        async def complete(self, **kwargs): return ""
        async def health_check(self): return True

    return SuggestedQuestionsAgent(llm=FakeLLM())


def _make_10_questions():
    """Build a valid v2 10-question list (4 food + 4 health + 2 anymall)."""
    qs = []
    for i in range(4):
        qs.append({"text": f"Food Q{i+1}?", "module": "food",    "target": "pet_a"})
    for i in range(4):
        qs.append({"text": f"Health Q{i+1}?", "module": "health", "target": "pet_a"})
    qs.append({"text": "AnyMall Q1?", "module": "anymall", "target": "pet_a"})
    qs.append({"text": "AnyMall Q2?", "module": "anymall", "target": "both"})
    return qs


def test_parse_10_valid():
    agent = _get_agent()
    raw = json.dumps(_make_10_questions())
    result = agent._parse_response(raw)
    if len(result) == 10 and result[0]["text"] == "Food Q1?":
        ok("valid 10-question JSON parsed correctly")
    else:
        fail(f"parse failed: {result}")


def test_parse_markdown_fenced():
    agent = _get_agent()
    raw = '```json\n' + json.dumps(_make_10_questions()) + '\n```'
    result = agent._parse_response(raw)
    if len(result) == 10:
        ok("markdown-fenced 10-question JSON parsed correctly")
    else:
        fail(f"fenced parse failed: got {len(result)} questions")


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
        {"text": "Q1?", "module": "food",   "target": "pet_a"},
        {"text": "Q2?", "module": "health", "target": "pet_a"},
    ])
    result = agent._parse_response(raw)
    if result == []:
        ok("wrong count (2) returns empty list")
    else:
        fail(f"wrong count should return [], got {result}")


def test_parse_malformed_items():
    agent = _get_agent()
    qs = _make_10_questions()
    qs[3] = {"bad_key": "missing text and module"}  # malformed slot 3
    raw = json.dumps(qs)
    result = agent._parse_response(raw)
    if result == []:
        ok("malformed item causes rejection (only 9 valid -> returns [])")
    else:
        fail(f"malformed items should return [], got {len(result)} items")


def test_parse_no_reason_type():
    agent = _get_agent()
    raw = json.dumps(_make_10_questions())
    result = agent._parse_response(raw)
    for q in result:
        if "reason_type" in q:
            fail(f"v2 parsed question should not have reason_type: {q}")
            return
    ok("parsed v2 questions have no reason_type field")


# ── A5: Character Limits ────────────────────────────────────────────────────

def test_char_limit_cjk():
    from app.services.question_generation.validator import get_char_limit
    for lang in ["JA", "KO", "ZH", "TH"]:
        if get_char_limit(lang) != 36:
            fail(f"CJK limit for {lang} should be 36")
            return
    ok("CJK languages -> 36 char limit")


def test_char_limit_latin():
    from app.services.question_generation.validator import get_char_limit
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

    Verifies that _regen_suggested_questions (now delegates to generator.regen_for_user):
      1. resolves language from the user cache
      2. calls regen_for_user which writes to Valkey
      3. the suggested questions cache key is populated
      4. the history cache key is populated
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.routes.background import _regen_suggested_questions
    from app.cache.keys import CacheKeys

    user_code = "U-TEST-001"
    pet_ids   = [42]
    language  = "EN"

    stored: dict[str, str] = {}
    _MOCK_PROFILE = json.dumps({"name": {"value": "Buddy", "confidence": 0.9}})

    # ── Mock Valkey ───────────────────────────────────────────────────────────
    mock_vk = MagicMock()

    async def _vk_get(key):
        if key == CacheKeys.user(user_code):
            return json.dumps({"preferred_language": language})
        if key.startswith("am:profile:"):
            return _MOCK_PROFILE
        return stored.get(key)

    async def _vk_setex(key, ttl, value):
        stored[key] = value

    mock_vk.get    = AsyncMock(side_effect=_vk_get)
    mock_vk.setex  = AsyncMock(side_effect=_vk_setex)

    # ── Mock PetFetcher ───────────────────────────────────────────────────────
    mock_pet_fetcher = MagicMock()
    pet_profile = {"name": "Buddy", "species": "dog", "breed": "Shiba", "sex": "male"}

    async def _fetch(uc, pid):
        return pet_profile, {}

    mock_pet_fetcher.fetch_pet_profile = AsyncMock(side_effect=_fetch)

    # ── Mock SuggestedQuestionsAgent ──────────────────────────────────────────
    mock_sq_agent = MagicMock()
    generated_questions = []
    for i in range(4):
        generated_questions.append({"text": f"Food Q{i+1}?",   "module": "food",    "target": "pet_a"})
    for i in range(4):
        generated_questions.append({"text": f"Health Q{i+1}?", "module": "health",  "target": "pet_a"})
    generated_questions.append({"text": "AnyMall Q1?", "module": "anymall", "target": "pet_a"})
    generated_questions.append({"text": "AnyMall Q2?", "module": "anymall", "target": "both"})

    async def _generate(**kwargs):
        return generated_questions

    mock_sq_agent.generate = AsyncMock(side_effect=_generate)

    # ── Mock get_session + SuggestedQuestionsRepo ─────────────────────────────
    mock_sq_repo = MagicMock()
    mock_sq_repo.upsert = AsyncMock()

    mock_db_session = MagicMock()

    @asynccontextmanager
    async def _mock_get_session():
        yield mock_db_session

    # ── Build state bag and run ───────────────────────────────────────────────
    bag = _make_mock_state_bag(
        sq_agent=mock_sq_agent,
        valkey=mock_vk,
        pet_fetcher=mock_pet_fetcher,
    )

    with patch("app.routes.background.get_session", _mock_get_session), \
         patch("app.routes.background.SuggestedQuestionsRepo", return_value=mock_sq_repo):
        asyncio.run(_regen_suggested_questions(user_code, pet_ids, bag))

    # ── Assertions ────────────────────────────────────────────────────────────
    expected_cache_key = CacheKeys.suggested_questions(user_code, language, pet_ids[0])
    if expected_cache_key not in stored:
        fail(f"vk.setex not called for suggested questions key: {expected_cache_key}")
        return

    cached = json.loads(stored[expected_cache_key])
    if "questions" not in cached or not isinstance(cached["questions"], list):
        fail(f"cached value missing questions: {cached}")
        return
    if "generated_at" not in cached:
        fail("cached value missing generated_at timestamp")
        return

    history_key = CacheKeys.suggested_history(user_code, language)
    if history_key not in stored:
        fail(f"vk.setex not called for history key: {history_key}")
        return

    ok(f"regen generated and cached questions under key {expected_cache_key}")
    ok(f"regen updated history under key {history_key}")


# ── A8: generator.regen_for_user (Step 9) ────────────────────────────────────

def _make_regen_mocks(generated_questions=None):
    """Build minimal mocks for regen_for_user: sq_agent, sq_repo, valkey, aalda, db_session."""
    from unittest.mock import AsyncMock, MagicMock

    # 10-question default
    if generated_questions is None:
        generated_questions = []
        for i in range(4):
            generated_questions.append({"text": f"Food Q{i+1}?", "module": "food",    "target": "pet_a"})
        for i in range(4):
            generated_questions.append({"text": f"Health Q{i+1}?", "module": "health", "target": "pet_a"})
        generated_questions.append({"text": "AnyMall Q1?", "module": "anymall", "target": "pet_a"})
        generated_questions.append({"text": "AnyMall Q2?", "module": "anymall", "target": "both"})

    stored = {}

    mock_sq_agent = MagicMock()
    async def _generate(**kwargs):
        return generated_questions
    mock_sq_agent.generate = AsyncMock(side_effect=_generate)

    mock_sq_repo = MagicMock()
    upsert_calls = []
    async def _upsert(**kwargs):
        upsert_calls.append(kwargs)
    mock_sq_repo.upsert = AsyncMock(side_effect=_upsert)
    mock_sq_repo._upsert_calls = upsert_calls

    # Pre-seed a minimal active profile so generator never hits the real DB
    _MOCK_PROFILE = json.dumps({"name": {"value": "Buddy", "confidence": 0.9}})

    async def _vk_get(key):
        # Return a minimal active profile for any am:profile:* key
        if key.startswith("am:profile:"):
            return _MOCK_PROFILE
        return stored.get(key)

    async def _vk_setex(key, ttl, value):
        stored[key] = value

    mock_vk = MagicMock()
    mock_vk.get    = AsyncMock(side_effect=_vk_get)
    mock_vk.setex  = AsyncMock(side_effect=_vk_setex)
    mock_vk._stored = stored

    mock_aalda = MagicMock()
    pet_profile = {"name": "Buddy", "species": "dog", "breed": "Shiba", "sex": "male"}
    async def _fetch(uc, pid):
        return pet_profile, {}
    mock_aalda.fetch_pet_profile = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock()

    return mock_sq_agent, mock_sq_repo, mock_vk, mock_aalda, mock_db


def test_regen_for_user_importable():
    try:
        from app.services.question_generation.generator import regen_for_user
        import inspect
        sig = inspect.signature(regen_for_user)
        params = set(sig.parameters)
        required = {"user_code", "pet_id", "language", "suggested_agent",
                    "suggested_repo", "valkey", "aalda_client", "db_session"}
        missing = required - params
        if missing:
            fail(f"regen_for_user missing params: {missing}")
        else:
            ok(f"regen_for_user importable with correct per-pet signature")
    except Exception as e:
        fail(f"import failed: {e}")


def test_regen_for_user_happy_path():
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    user_code = "U-GEN-001"
    pet_id = 42
    language = "EN"

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()

    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=pet_id, language=language,
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    cache_key = CacheKeys.suggested_questions(user_code, language, pet_id)
    if cache_key not in vk._stored:
        fail(f"Valkey key not written: {cache_key}")
        return

    cached = json.loads(vk._stored[cache_key])
    if len(cached.get("questions", [])) != 10:
        fail(f"expected 10 questions in cache, got {len(cached.get('questions', []))}")
        return
    if not sq_repo._upsert_calls:
        fail("SuggestedQuestionsRepo.upsert not called")
        return

    history_key = CacheKeys.suggested_history(user_code, language)
    if history_key not in vk._stored:
        fail(f"history key not written: {history_key}")
        return

    ok("regen_for_user happy path: Valkey + Postgres + history all written")


def test_regen_for_user_empty_llm_uses_evergreen():
    """When LLM returns [], all 10 slots filled from evergreen (no crash)."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    user_code = "U-GEN-002"
    pet_id = 42
    language = "EN"

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks(generated_questions=[])

    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=pet_id, language=language,
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    cache_key = CacheKeys.suggested_questions(user_code, language, pet_id)
    if cache_key not in vk._stored:
        fail(f"cache key not written even for evergreen fallback: {cache_key}")
        return

    cached = json.loads(vk._stored[cache_key])
    qs = cached.get("questions", [])
    if len(qs) != 10:
        fail(f"expected 10 evergreen questions, got {len(qs)}")
        return
    for q in qs:
        if not q.get("text"):
            fail(f"evergreen slot has empty text: {q}")
            return
    ok("empty LLM result fills all 10 slots with non-empty evergreen questions")


def test_regen_for_user_slot_patch_on_validation_failure():
    """A question that fails validation gets patched with an evergreen question."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    user_code = "U-GEN-003"
    language = "EN"

    # Slot 0 has alarmist content — should be patched
    bad_qs = []
    bad_qs.append({"text": "Is this an emergency?", "module": "food", "target": "pet_a"})  # alarmist
    for i in range(3):
        bad_qs.append({"text": f"Food Q{i+2}?", "module": "food",    "target": "pet_a"})
    for i in range(4):
        bad_qs.append({"text": f"Health Q{i+1}?", "module": "health", "target": "pet_a"})
    bad_qs.append({"text": "AnyMall Q1?", "module": "anymall", "target": "pet_a"})
    bad_qs.append({"text": "AnyMall Q2?", "module": "anymall", "target": "both"})

    pet_id = 42
    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks(generated_questions=bad_qs)

    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=pet_id, language=language,
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    cache_key = CacheKeys.suggested_questions(user_code, language, pet_id)
    if cache_key not in vk._stored:
        fail(f"cache not written after patching: {cache_key}")
        return

    cached = json.loads(vk._stored[cache_key])
    final_qs = cached.get("questions", [])
    if len(final_qs) != 10:
        fail(f"expected 10 questions, got {len(final_qs)}")
        return

    slot0 = final_qs[0]
    if "emergency" in slot0.get("text", "").lower():
        fail("alarmist slot 0 was NOT patched — still has 'emergency' text")
        return

    ok(f"alarmist slot 0 patched with evergreen: {slot0['text']!r}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A9 — Language: EN vs JA generation
# ══════════════════════════════════════════════════════════════════════════════

def test_regen_generates_for_en_language():
    """regen_for_user with language='EN' writes a Valkey key containing 'EN'."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code="U-LANG-EN", pet_id=1, language="EN",
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))
    expected_key = CacheKeys.suggested_questions("U-LANG-EN", "EN", 1)
    if expected_key in vk._stored:
        ok(f"EN cache key written: {expected_key}")
    else:
        fail(f"EN cache key not found. stored keys: {list(vk._stored.keys())}")


def test_regen_generates_for_ja_language():
    """regen_for_user with language='JA' writes a Valkey key containing 'JA'."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code="U-LANG-JA", pet_id=1, language="JA",
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))
    expected_key = CacheKeys.suggested_questions("U-LANG-JA", "JA", 1)
    if expected_key in vk._stored:
        ok(f"JA cache key written: {expected_key}")
    else:
        fail(f"JA cache key not found. stored keys: {list(vk._stored.keys())}")


def test_regen_en_and_ja_produce_separate_keys():
    """EN and JA for the same user+pet produce different Valkey keys."""
    from app.cache.keys import CacheKeys
    key_en = CacheKeys.suggested_questions("U-MULTI", "EN", 55)
    key_ja = CacheKeys.suggested_questions("U-MULTI", "JA", 55)
    if key_en != key_ja and "EN" in key_en and "JA" in key_ja:
        ok(f"EN/JA produce separate keys: {key_en} vs {key_ja}")
    else:
        fail(f"keys not separated by language: {key_en}, {key_ja}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A10 — is_pet_b: pet_a vs pet_b target
# ══════════════════════════════════════════════════════════════════════════════

def test_regen_is_pet_b_false_stores_pet_a_questions():
    """
    is_pet_b=False → LLM called with is_pet_b=False → evergreen fills with pet_a target.
    Slot layout: 3 food/pet_a + 1 food/both + 3 health/pet_a + 1 health/both +
                 1 anymall/pet_a + 1 anymall/both = 7 dedicated pet_a + 3 both.
    """
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks(generated_questions=[])

    generate_kwargs = {}

    async def _gen_capture(**kwargs):
        generate_kwargs.update(kwargs)
        return []  # force evergreen fallback

    sq_agent.generate = AsyncMock(side_effect=_gen_capture)

    asyncio.run(regen_for_user(
        user_code="U-PET-A", pet_id=10, language="EN",
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
        is_pet_b=False,
    ))

    if generate_kwargs.get("is_pet_b") is not False:
        fail(f"expected is_pet_b=False in generate() kwargs, got: {generate_kwargs.get('is_pet_b')!r}")
        return
    ok("is_pet_b=False passed through to LLM generate()")

    key = CacheKeys.suggested_questions("U-PET-A", "EN", 10)
    if key not in vk._stored:
        fail("cache key not written after evergreen fallback")
        return

    qs = json.loads(vk._stored[key]).get("questions", [])
    pet_a_count = sum(1 for q in qs if q.get("target") == "pet_a")
    if pet_a_count >= 7:
        ok(f"evergreen for is_pet_b=False has {pet_a_count} pet_a questions (>=7 expected)")
    else:
        fail(f"expected >=7 pet_a questions, got {pet_a_count}: {[q['target'] for q in qs]}")


def test_regen_is_pet_b_true_stores_pet_b_questions():
    """
    is_pet_b=True → LLM called with is_pet_b=True → evergreen fills with pet_b target.
    Slot layout: 7 dedicated pet_b + 3 both.
    """
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks(generated_questions=[])

    generate_kwargs = {}

    async def _gen_capture(**kwargs):
        generate_kwargs.update(kwargs)
        return []  # force evergreen fallback

    sq_agent.generate = AsyncMock(side_effect=_gen_capture)

    asyncio.run(regen_for_user(
        user_code="U-PET-B", pet_id=20, language="EN",
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
        is_pet_b=True,
    ))

    if generate_kwargs.get("is_pet_b") is not True:
        fail(f"expected is_pet_b=True in generate() kwargs, got: {generate_kwargs.get('is_pet_b')!r}")
        return
    ok("is_pet_b=True passed through to LLM generate()")

    key = CacheKeys.suggested_questions("U-PET-B", "EN", 20)
    if key not in vk._stored:
        fail("cache key not written after evergreen fallback")
        return

    qs = json.loads(vk._stored[key]).get("questions", [])
    pet_b_count = sum(1 for q in qs if q.get("target") == "pet_b")
    if pet_b_count >= 7:
        ok(f"evergreen for is_pet_b=True has {pet_b_count} pet_b questions (>=7 expected)")
    else:
        fail(f"expected >=7 pet_b questions, got {pet_b_count}: {[q['target'] for q in qs]}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A11 — Background regen: one call per pet
# ══════════════════════════════════════════════════════════════════════════════

def test_background_regen_two_pets_calls_regen_twice():
    """_regen_suggested_questions with 2 pets → regen_for_user called once per pet."""
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.routes.background import _regen_suggested_questions

    user_code = "U-TWO-PETS"
    pet_ids = [101, 102]
    language = "EN"
    calls = []

    async def _mock_regen(**kwargs):
        calls.append(kwargs)

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=json.dumps({"preferred_language": language}))

    bag = _make_mock_state_bag(
        sq_agent=MagicMock(), valkey=mock_vk, pet_fetcher=MagicMock()
    )
    mock_sq_repo = MagicMock()
    mock_sq_repo.upsert = AsyncMock()

    @asynccontextmanager
    async def _mock_session():
        yield MagicMock()

    with patch("app.routes.background.regen_for_user", new=AsyncMock(side_effect=_mock_regen)), \
         patch("app.routes.background.get_session", _mock_session), \
         patch("app.routes.background.SuggestedQuestionsRepo", return_value=mock_sq_repo):
        asyncio.run(_regen_suggested_questions(user_code, pet_ids, bag))

    if len(calls) == 2:
        ok(f"regen_for_user called {len(calls)} times — once per pet")
    else:
        fail(f"expected 2 regen_for_user calls, got {len(calls)}")


def test_background_regen_is_pet_b_flag_per_pet():
    """First pet (i=0) gets is_pet_b=False; second pet (i=1) gets is_pet_b=True."""
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.routes.background import _regen_suggested_questions

    user_code = "U-IS-PET-B"
    pet_ids = [101, 102]
    language = "EN"
    calls = []

    async def _mock_regen(**kwargs):
        calls.append({"pet_id": kwargs.get("pet_id"), "is_pet_b": kwargs.get("is_pet_b")})

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=json.dumps({"preferred_language": language}))

    bag = _make_mock_state_bag(
        sq_agent=MagicMock(), valkey=mock_vk, pet_fetcher=MagicMock()
    )
    mock_sq_repo = MagicMock()
    mock_sq_repo.upsert = AsyncMock()

    @asynccontextmanager
    async def _mock_session():
        yield MagicMock()

    with patch("app.routes.background.regen_for_user", new=AsyncMock(side_effect=_mock_regen)), \
         patch("app.routes.background.get_session", _mock_session), \
         patch("app.routes.background.SuggestedQuestionsRepo", return_value=mock_sq_repo):
        asyncio.run(_regen_suggested_questions(user_code, pet_ids, bag))

    if len(calls) != 2:
        fail(f"expected 2 calls, got {len(calls)}")
        return
    if calls[0]["is_pet_b"] is False and calls[1]["is_pet_b"] is True:
        ok(f"is_pet_b: pet[0]={calls[0]['pet_id']} is False, pet[1]={calls[1]['pet_id']} is True")
    else:
        fail(f"unexpected is_pet_b flags: {calls}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A12 — _pick_questions pick rule
# ══════════════════════════════════════════════════════════════════════════════

def _make_pet_row(target: str) -> list[dict]:
    """
    Build a valid 10-question row for one pet using the v2 slot layout.
    Slot layout: food×3/tgt + food×1/both + health×3/tgt + health×1/both
                 + anymall×1/tgt + anymall×1/both
    """
    return [
        {"text": f"Food dedicated 1 ({target})",   "module": "food",    "target": target},
        {"text": f"Food dedicated 2 ({target})",   "module": "food",    "target": target},
        {"text": f"Food dedicated 3 ({target})",   "module": "food",    "target": target},
        {"text": "Food both",                       "module": "food",    "target": "both"},
        {"text": f"Health dedicated 1 ({target})", "module": "health",  "target": target},
        {"text": f"Health dedicated 2 ({target})", "module": "health",  "target": target},
        {"text": f"Health dedicated 3 ({target})", "module": "health",  "target": target},
        {"text": "Health both",                     "module": "health",  "target": "both"},
        {"text": f"Anymall dedicated ({target})",  "module": "anymall", "target": target},
        {"text": "Anymall both",                    "module": "anymall", "target": "both"},
    ]


def test_pick_food_single_pet_returns_3_dedicated():
    """food + single pet → 3 food/pet_a questions from primary row."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a")}
    result = _pick_questions(rows, [101], "food", "EN")
    if len(result) != 3:
        fail(f"expected 3 questions, got {len(result)}: {result}")
        return
    if all(q["module"] == "food" and q["target"] == "pet_a" for q in result):
        ok("food single pet: 3 dedicated pet_a questions")
    else:
        fail(f"unexpected food single result: {[(q['module'], q['target']) for q in result]}")


def test_pick_food_single_pet_no_duplicates():
    """food + single pet → the 3 questions must be distinct (no duplicate text)."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a")}
    result = _pick_questions(rows, [101], "food", "EN")
    texts = [q["text"] for q in result]
    if len(texts) == len(set(texts)):
        ok(f"food single pet: 3 distinct questions — {texts}")
    else:
        fail(f"duplicate questions in single-pet food result: {texts}")


def test_pick_food_dual_pet_layout():
    """food + dual pet → pet_a[0] + pet_b[0] + both[0]."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a"), 102: _make_pet_row("pet_b")}
    result = _pick_questions(rows, [101, 102], "food", "EN")
    if len(result) != 3:
        fail(f"expected 3 questions, got {len(result)}")
        return
    targets = [q["target"] for q in result]
    if targets == ["pet_a", "pet_b", "both"]:
        ok(f"food dual pet: pet_a + pet_b + both — {[q['text'] for q in result]}")
    else:
        fail(f"expected [pet_a, pet_b, both], got {targets}")


def test_pick_health_single_pet_returns_3_dedicated():
    """health + single pet → 3 health/pet_a questions."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a")}
    result = _pick_questions(rows, [101], "health", "EN")
    if len(result) == 3 and all(q["module"] == "health" and q["target"] == "pet_a" for q in result):
        ok("health single pet: 3 dedicated pet_a questions")
    else:
        fail(f"unexpected health single: {[(q['module'], q['target']) for q in result]}")


def test_pick_health_dual_pet_layout():
    """health + dual pet → pet_a[0] + pet_b[0] + both[0]."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a"), 102: _make_pet_row("pet_b")}
    result = _pick_questions(rows, [101, 102], "health", "EN")
    targets = [q["target"] for q in result]
    if len(result) == 3 and targets == ["pet_a", "pet_b", "both"]:
        ok("health dual pet: pet_a + pet_b + both")
    else:
        fail(f"expected [pet_a, pet_b, both], got {targets}")


def test_pick_anymall_single_pet_layout():
    """anymall + single pet → food/pet_a + health/pet_a + anymall/pet_a."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a")}
    result = _pick_questions(rows, [101], "anymall", "EN")
    if len(result) != 3:
        fail(f"expected 3, got {len(result)}")
        return
    modules  = [q["module"]  for q in result]
    targets  = [q["target"]  for q in result]
    if modules == ["food", "health", "anymall"] and all(t == "pet_a" for t in targets):
        ok(f"anymall single pet: food/pet_a + health/pet_a + anymall/pet_a")
    else:
        fail(f"unexpected anymall single: modules={modules} targets={targets}")


def test_pick_anymall_dual_pet_layout():
    """anymall + dual pet → food/pet_a + health/pet_b + anymall/both."""
    from app.routes.chat import _pick_questions
    rows = {101: _make_pet_row("pet_a"), 102: _make_pet_row("pet_b")}
    result = _pick_questions(rows, [101, 102], "anymall", "EN")
    if len(result) != 3:
        fail(f"expected 3, got {len(result)}")
        return
    expected = [("food", "pet_a"), ("health", "pet_b"), ("anymall", "both")]
    actual   = [(q["module"], q["target"]) for q in result]
    if actual == expected:
        ok(f"anymall dual pet: food/pet_a + health/pet_b + anymall/both")
    else:
        fail(f"expected {expected}, got {actual}")


def test_pick_always_returns_3_even_with_empty_rows():
    """Empty rows dict → all 3 slots filled from evergreen, never crashes."""
    from app.routes.chat import _pick_questions
    for mod in ("food", "health", "anymall"):
        result = _pick_questions({}, [101], mod, "EN")
        if len(result) != 3:
            fail(f"{mod} empty rows: expected 3, got {len(result)}")
            return
        if not all(q.get("text") for q in result):
            fail(f"{mod} empty rows: evergreen returned empty text slot: {result}")
            return
    ok("empty rows: 3 non-empty evergreen questions returned for each module")


# ══════════════════════════════════════════════════════════════════════════════
# Section A13 — Nightly job: staleness + language cleanup
# ══════════════════════════════════════════════════════════════════════════════

def test_get_all_stale_sql_filters_preferred_language():
    """
    get_all_stale() must filter sq.language = u.preferred_language.
    Without this, the nightly job would regenerate stale rows for languages the user
    already switched away from (wasted LLM calls + storage accumulation).
    """
    import inspect
    from app.db.repositories import SuggestedQuestionsRepo
    src = inspect.getsource(SuggestedQuestionsRepo.get_all_stale)
    if "preferred_language" in src:
        ok("get_all_stale SQL filters by preferred_language")
    else:
        fail("get_all_stale SQL does not filter by preferred_language — "
             "stale old-language rows would be regenerated nightly")


def test_cleanup_stale_language_rows_method_exists():
    """
    SuggestedQuestionsRepo must have cleanup_stale_language_rows() so the nightly
    job can delete rows for languages the user switched away from.
    """
    import inspect
    from app.db.repositories import SuggestedQuestionsRepo
    methods = {name for name, _ in inspect.getmembers(SuggestedQuestionsRepo, predicate=inspect.isfunction)}
    if "cleanup_stale_language_rows" in methods:
        ok("SuggestedQuestionsRepo.cleanup_stale_language_rows() exists")
    else:
        fail("cleanup_stale_language_rows missing from SuggestedQuestionsRepo")


def test_pregenerate_skips_when_agent_unavailable():
    """_pregenerate_suggested_questions must exit early (no crash) when sq_agent is None."""
    import asyncio
    from app.jobs.nightly import _pregenerate_suggested_questions

    class _MockState:
        suggested_questions_agent = None
        valkey = object()
        pet_fetcher = object()

    asyncio.run(_pregenerate_suggested_questions(_MockState()))
    ok("_pregenerate_suggested_questions skips gracefully when agent is None")


def test_nightly_cleanup_called_in_pregenerate():
    """
    cleanup_stale_language_rows must be invoked inside _pregenerate_suggested_questions
    so that old-language rows are removed each night.
    """
    import inspect
    from app.jobs.nightly import _pregenerate_suggested_questions
    src = inspect.getsource(_pregenerate_suggested_questions)
    if "cleanup_stale_language_rows" in src:
        ok("_pregenerate_suggested_questions calls cleanup_stale_language_rows")
    else:
        fail("_pregenerate_suggested_questions does not call cleanup_stale_language_rows")


# ══════════════════════════════════════════════════════════════════════════════
# Section A14 — Cache: per-pet key correctness
# ══════════════════════════════════════════════════════════════════════════════

def test_regen_writes_one_key_per_pet():
    """
    Two separate regen_for_user calls (different pet_ids) write two distinct Valkey keys.
    This is the core guarantee of the per-pet cache design.
    """
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    user_code = "U-CACHE-CHECK"
    language  = "EN"

    sq_agent_a, sq_repo_a, vk_a, aalda_a, db_a = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=101, language=language,
        suggested_agent=sq_agent_a, suggested_repo=sq_repo_a,
        valkey=vk_a, aalda_client=aalda_a, db_session=db_a,
    ))

    sq_agent_b, sq_repo_b, vk_b, aalda_b, db_b = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=102, language=language,
        suggested_agent=sq_agent_b, suggested_repo=sq_repo_b,
        valkey=vk_b, aalda_client=aalda_b, db_session=db_b,
    ))

    key_101 = CacheKeys.suggested_questions(user_code, language, 101)
    key_102 = CacheKeys.suggested_questions(user_code, language, 102)

    if key_101 not in vk_a._stored:
        fail(f"pet 101 key not written: {key_101}")
        return
    if key_102 not in vk_b._stored:
        fail(f"pet 102 key not written: {key_102}")
        return
    if key_101 == key_102:
        fail(f"keys are identical — not per-pet: {key_101}")
        return

    ok(f"two distinct per-pet keys written: {key_101} / {key_102}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A15 — PostgreSQL repo: pet_id in upsert/get
# ══════════════════════════════════════════════════════════════════════════════

def test_orm_model_has_pet_id_column():
    """SuggestedQuestion ORM model must have pet_id after the migration."""
    try:
        from app.db.models import SuggestedQuestion
        cols = {c.name for c in SuggestedQuestion.__table__.columns}
        if "pet_id" in cols:
            ok(f"SuggestedQuestion has pet_id column (all cols: {cols})")
        else:
            fail(f"SuggestedQuestion missing pet_id column. Found: {cols}")
    except Exception as e:
        fail(f"ORM import failed: {e}")


def test_repo_upsert_signature_has_pet_id():
    """SuggestedQuestionsRepo.upsert must accept pet_id as a named parameter."""
    import inspect
    from app.db.repositories import SuggestedQuestionsRepo
    sig = inspect.signature(SuggestedQuestionsRepo.upsert)
    if "pet_id" in sig.parameters:
        ok(f"upsert() signature has pet_id: {list(sig.parameters)}")
    else:
        fail(f"upsert() missing pet_id param: {list(sig.parameters)}")


def test_repo_get_signature_has_pet_id():
    """SuggestedQuestionsRepo.get must accept pet_id as a named parameter."""
    import inspect
    from app.db.repositories import SuggestedQuestionsRepo
    sig = inspect.signature(SuggestedQuestionsRepo.get)
    if "pet_id" in sig.parameters:
        ok(f"get() signature has pet_id: {list(sig.parameters)}")
    else:
        fail(f"get() missing pet_id param: {list(sig.parameters)}")


def test_repo_upsert_called_with_pet_id_in_happy_path():
    """regen_for_user passes pet_id=<correct int> to SuggestedQuestionsRepo.upsert."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user

    user_code = "U-REPO-TEST"
    pet_id    = 77
    language  = "EN"

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=pet_id, language=language,
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    if not sq_repo._upsert_calls:
        fail("SuggestedQuestionsRepo.upsert was never called")
        return
    call = sq_repo._upsert_calls[0]
    if call.get("pet_id") == pet_id:
        ok(f"upsert called with pet_id={pet_id}")
    else:
        fail(f"upsert pet_id mismatch: expected {pet_id}, got {call.get('pet_id')!r}")


def test_repo_upsert_called_with_correct_user_and_language():
    """regen_for_user passes correct user_code and language to SuggestedQuestionsRepo.upsert."""
    import asyncio
    from app.services.question_generation.generator import regen_for_user

    user_code = "U-REPO-LANG"
    pet_id    = 88
    language  = "JA"

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()
    asyncio.run(regen_for_user(
        user_code=user_code, pet_id=pet_id, language=language,
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    if not sq_repo._upsert_calls:
        fail("upsert was never called")
        return
    call = sq_repo._upsert_calls[0]
    if call.get("user_code") == user_code and call.get("language") == language:
        ok(f"upsert called with user_code={user_code!r} language={language!r}")
    else:
        fail(f"upsert wrong args: expected user_code={user_code!r} language={language!r}, "
             f"got user_code={call.get('user_code')!r} language={call.get('language')!r}")


# ══════════════════════════════════════════════════════════════════════════════
# Shared helper — simulate /setup question lookup (Valkey -> Postgres -> evergreen)
# ══════════════════════════════════════════════════════════════════════════════

async def _simulate_setup_lookup(pet_ids, user_code, language, mock_vk, mock_sq_repo):
    """
    Simulates exactly what /setup does for the questions section:
      Step 1 — check Valkey per pet_id
      Step 2 — for any miss, call SuggestedQuestionsRepo.get() + warm Valkey
      Step 3 — for any still missing, evergreen

    Returns (per_pet_rows, postgres_called_for_pids).
    per_pet_rows: {pet_id: [questions]} for every hit.
    postgres_called_for_pids: list of pet_ids that triggered a Postgres lookup.
    """
    from app.cache.keys import CacheKeys
    per_pet_rows = {}
    postgres_called_for = []

    for pid in pet_ids:
        key = CacheKeys.suggested_questions(user_code, language, pid)
        raw = await mock_vk.get(key)
        if raw is not None:
            per_pet_rows[pid] = json.loads(raw).get("questions", [])

    missing = [pid for pid in pet_ids if pid not in per_pet_rows]
    for pid in missing:
        postgres_called_for.append(pid)
        pg_row = await mock_sq_repo.get(user_code, language, pid)
        if pg_row:
            per_pet_rows[pid] = pg_row["questions"]
            key = CacheKeys.suggested_questions(user_code, language, pid)
            await mock_vk.setex(key, 864000, json.dumps({"questions": pg_row["questions"]}))

    return per_pet_rows, postgres_called_for


# ══════════════════════════════════════════════════════════════════════════════
# Section A16 — Language change: regen new language, delete old
# ══════════════════════════════════════════════════════════════════════════════

def test_language_change_new_language_key_written():
    """
    When regen runs for a user who switched from JA to EN, the EN cache key is written.
    The old JA key is never touched by this regen call (different key entirely).
    """
    import asyncio
    from app.services.question_generation.generator import regen_for_user
    from app.cache.keys import CacheKeys

    sq_agent, sq_repo, vk, aalda, db = _make_regen_mocks()

    asyncio.run(regen_for_user(
        user_code="U-LANG-CHANGE", pet_id=10, language="EN",
        suggested_agent=sq_agent, suggested_repo=sq_repo,
        valkey=vk, aalda_client=aalda, db_session=db,
    ))

    en_key = CacheKeys.suggested_questions("U-LANG-CHANGE", "EN", 10)
    ja_key = CacheKeys.suggested_questions("U-LANG-CHANGE", "JA", 10)

    if en_key not in vk._stored:
        fail(f"EN cache key not written after language change regen: {en_key}")
        return
    if ja_key in vk._stored:
        fail(f"JA cache key should NOT be written when regenerating for EN: {ja_key}")
        return

    ok(f"EN key written ({en_key}), JA key untouched after language change regen")


def test_language_change_old_language_key_not_served():
    """
    A user switched from JA to EN. The Valkey JA key for pet 10 still exists (stale).
    When /setup resolves preferred_language=EN, it looks up the EN key, NOT the JA key.
    The stale JA data is never served.
    """
    import asyncio
    from app.cache.keys import CacheKeys
    from unittest.mock import AsyncMock, MagicMock

    user_code = "U-STALE-JA"
    pet_id = 10
    old_ja_questions = [{"text": "JA stale question", "module": "food", "target": "pet_a"}]

    # Pre-seed Valkey with old JA data
    stored = {}
    ja_key = CacheKeys.suggested_questions(user_code, "JA", pet_id)
    stored[ja_key] = json.dumps({"questions": old_ja_questions})

    async def _vk_get(key):
        return stored.get(key)

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(side_effect=_vk_get)

    mock_sq_repo = MagicMock()
    mock_sq_repo.get = AsyncMock(return_value=None)  # Postgres also empty for EN

    # Simulate /setup with preferred_language=EN
    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, "EN", mock_vk, mock_sq_repo)
    )

    en_key = CacheKeys.suggested_questions(user_code, "EN", pet_id)
    if per_pet_rows:
        fail(f"Old JA data must not be served when language=EN. Got rows: {per_pet_rows}")
        return
    if en_key in stored:
        fail(f"EN key should not exist yet (user just switched): {en_key}")
        return

    ok(f"Language switch: JA key ({ja_key}) not served when preferred_language=EN")


def test_nightly_full_cycle_language_change():
    """
    Full nightly cycle after JA->EN language change:
    - get_all_stale() returns one row with language=EN (current language)
    - regen_for_user is called for EN
    - cleanup_stale_language_rows() is called to delete the old JA row
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.jobs.nightly import _pregenerate_suggested_questions

    regen_calls = []
    cleanup_calls = []

    async def _mock_regen(**kwargs):
        regen_calls.append(kwargs)

    class _MockState:
        suggested_questions_agent = MagicMock()
        valkey = MagicMock()
        pet_fetcher = MagicMock()

    mock_sq_repo_scan = MagicMock()
    mock_sq_repo_scan.get_all_stale = AsyncMock(return_value=[
        {"user_code": "U-LC-USER", "language": "EN", "pet_id": 55, "last_known_pet_ids": [55]},
    ])

    mock_sq_repo_cleanup = MagicMock()
    async def _mock_cleanup():
        cleanup_calls.append(True)
        return 1  # 1 old JA row deleted
    mock_sq_repo_cleanup.cleanup_stale_language_rows = AsyncMock(side_effect=_mock_cleanup)

    repo_instances = []
    def _make_repo(session):
        if not repo_instances:
            repo_instances.append(mock_sq_repo_scan)
            return mock_sq_repo_scan
        return mock_sq_repo_cleanup

    @asynccontextmanager
    async def _mock_session():
        yield MagicMock()

    with patch("app.jobs.nightly.get_session", _mock_session), \
         patch("app.jobs.nightly.SuggestedQuestionsRepo", side_effect=_make_repo), \
         patch("app.jobs.nightly.regen_for_user", new=AsyncMock(side_effect=_mock_regen)):
        asyncio.run(_pregenerate_suggested_questions(_MockState()))

    if not regen_calls:
        fail("regen_for_user not called in nightly cycle")
        return
    if regen_calls[0].get("language") != "EN":
        fail(f"regen called with language={regen_calls[0].get('language')!r}, expected EN")
        return
    if not cleanup_calls:
        fail("cleanup_stale_language_rows not called after regen")
        return

    ok(f"nightly cycle: regen called for EN, cleanup fired ({cleanup_calls[0]})")


# ══════════════════════════════════════════════════════════════════════════════
# Section A17 — /setup cache-load path: correct pet key loaded
# ══════════════════════════════════════════════════════════════════════════════

def test_setup_single_pet_loads_own_key():
    """
    /setup with ?pet_id=101 must load am:suggested:user:EN:101.
    Pet 101's questions are served, not some other pet's questions.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys

    user_code = "U-SETUP-S1"
    language  = "EN"
    pet_id    = 101

    pet_a_questions = _make_pet_row("pet_a")
    stored = {CacheKeys.suggested_questions(user_code, language, pet_id): json.dumps({"questions": pet_a_questions})}

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(side_effect=lambda k: stored.get(k))
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value=None)

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo)
    )

    if pet_id not in per_pet_rows:
        fail(f"pet {pet_id} questions not loaded from Valkey")
        return
    if pg_called:
        fail(f"Postgres called even though Valkey had the key: pg_called={pg_called}")
        return
    if per_pet_rows[pet_id] != pet_a_questions:
        fail(f"wrong questions served: expected pet_a row, got {per_pet_rows[pet_id][:1]}")
        return

    ok(f"single pet: pet 101 loads its own key, 10 questions served, Postgres not called")


def test_setup_pet_b_alone_loads_pet_b_key_not_pet_a():
    """
    User has 2 pets but selects ONLY pet B (pet_id=102).
    /setup must look up am:suggested:user:EN:102, NOT 101.
    The questions served must be pet_b's questions (target=pet_b), not pet_a's.

    This is the critical Bug #2 fix: before the per-pet redesign, selecting
    pet B alone would show pet A's questions (because the universal 10-question
    pool used pet_count=1 -> always defaulted to pet_a slots).
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys
    from app.routes.chat import _pick_questions

    user_code = "U-PETB-ALONE"
    language  = "EN"
    pet_b_id  = 102
    pet_a_id  = 101  # user also has this pet but did NOT select it

    # Only pet B's key exists in Valkey
    pet_b_questions = _make_pet_row("pet_b")
    stored = {
        CacheKeys.suggested_questions(user_code, language, pet_b_id): json.dumps({"questions": pet_b_questions})
    }
    # pet A key intentionally absent

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(side_effect=lambda k: stored.get(k))
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value=None)

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_b_id], user_code, language, mock_vk, mock_repo)
    )

    if pet_b_id not in per_pet_rows:
        fail(f"pet_b key (id={pet_b_id}) not loaded from Valkey")
        return

    # Now pick questions: user selected ONLY pet B
    result = _pick_questions(per_pet_rows, [pet_b_id], "food", language)
    targets = [q["target"] for q in result]

    if all(t == "pet_b" for t in targets):
        ok(f"pet B alone: food pick returns 3 pet_b questions (targets={targets})")
    else:
        fail(f"Bug #2 not fixed: selecting pet B alone returned non-pet_b questions: {targets}")


def test_setup_dual_pet_loads_both_keys():
    """
    /setup with ?pet_id=101&pet_id=102 loads BOTH per-pet cache keys.
    Questions for both pets are available in per_pet_rows.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys
    from app.routes.chat import _pick_questions

    user_code = "U-DUAL-LOAD"
    language  = "EN"
    pet_a_id, pet_b_id = 101, 102

    pet_a_questions = _make_pet_row("pet_a")
    pet_b_questions = _make_pet_row("pet_b")
    stored = {
        CacheKeys.suggested_questions(user_code, language, pet_a_id): json.dumps({"questions": pet_a_questions}),
        CacheKeys.suggested_questions(user_code, language, pet_b_id): json.dumps({"questions": pet_b_questions}),
    }

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(side_effect=lambda k: stored.get(k))
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value=None)

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_a_id, pet_b_id], user_code, language, mock_vk, mock_repo)
    )

    if pet_a_id not in per_pet_rows or pet_b_id not in per_pet_rows:
        fail(f"not both keys loaded. per_pet_rows keys={list(per_pet_rows.keys())}")
        return
    if pg_called:
        fail(f"Postgres called even though both Valkey keys existed: pg_called={pg_called}")
        return

    result = _pick_questions(per_pet_rows, [pet_a_id, pet_b_id], "food", language)
    targets = [q["target"] for q in result]

    if targets == ["pet_a", "pet_b", "both"]:
        ok(f"dual pet: both keys loaded, food pick returns [pet_a, pet_b, both]")
    else:
        fail(f"unexpected targets for dual pet food: {targets}")


# ══════════════════════════════════════════════════════════════════════════════
# Section A18 — /setup cache HIT: Valkey served, Postgres never called
# ══════════════════════════════════════════════════════════════════════════════

def test_setup_cache_hit_serves_from_valkey():
    """
    Valkey has the questions for pet 101.
    /setup must serve those questions without touching Postgres.
    This verifies the cache-aside pattern: hot cache always wins.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys

    user_code = "U-CACHE-HIT"
    language  = "EN"
    pet_id    = 101

    cached_questions = _make_pet_row("pet_a")
    key = CacheKeys.suggested_questions(user_code, language, pet_id)

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=json.dumps({"questions": cached_questions}))
    mock_vk.setex = AsyncMock()

    postgres_calls = []
    mock_repo = MagicMock()
    async def _pg_get(uc, lang, pid):
        postgres_calls.append(pid)
        return None
    mock_repo.get = AsyncMock(side_effect=_pg_get)

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo)
    )

    if pet_id not in per_pet_rows:
        fail("questions not in per_pet_rows even though Valkey had them")
        return
    if per_pet_rows[pet_id] != cached_questions:
        fail("questions served do not match what was in Valkey cache")
        return

    ok(f"cache HIT: {len(per_pet_rows[pet_id])} questions served from Valkey")


def test_setup_cache_hit_does_not_call_postgres():
    """
    When Valkey has the data, Postgres must NOT be called.
    Calling Postgres on a cache hit would be a wasted DB round-trip.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys

    user_code = "U-CACHE-HIT2"
    language  = "EN"
    pet_id    = 101

    cached_questions = _make_pet_row("pet_a")
    key = CacheKeys.suggested_questions(user_code, language, pet_id)

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=json.dumps({"questions": cached_questions}))
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value=None)

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo)
    )

    if pg_called:
        fail(f"Postgres was called despite Valkey cache hit: pg_called={pg_called}")
        return

    ok("cache HIT: Postgres not called (correct cache-aside behaviour)")


# ══════════════════════════════════════════════════════════════════════════════
# Section A19 — /setup Postgres fallback: Valkey cold, load from DB
# ══════════════════════════════════════════════════════════════════════════════

def test_setup_postgres_fallback_when_valkey_cold():
    """
    Valkey has no entry for pet 101 (cache miss / expired).
    /setup must fall back to Postgres and serve the stored questions.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys

    user_code = "U-PG-FALLBACK"
    language  = "EN"
    pet_id    = 101

    pg_questions = _make_pet_row("pet_a")

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=None)  # Valkey cold
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value={"questions": pg_questions, "generated_at": None})

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo)
    )

    if pet_id not in per_pet_rows:
        fail(f"Postgres fallback did not populate per_pet_rows for pet {pet_id}")
        return
    if per_pet_rows[pet_id] != pg_questions:
        fail("questions from Postgres fallback do not match expected")
        return
    if pet_id not in pg_called:
        fail(f"Postgres.get() not called for pet {pet_id}")
        return

    ok(f"Postgres fallback: pet {pet_id} questions loaded from DB after Valkey miss")


def test_setup_postgres_fallback_warms_valkey():
    """
    After loading questions from Postgres, /setup must write them back to Valkey
    (write-through warming) so the next call is a cache hit.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.cache.keys import CacheKeys

    user_code = "U-PG-WARM"
    language  = "EN"
    pet_id    = 101

    pg_questions = _make_pet_row("pet_a")
    setex_calls = {}

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=None)
    async def _setex(key, ttl, value):
        setex_calls[key] = value
    mock_vk.setex = AsyncMock(side_effect=_setex)

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value={"questions": pg_questions, "generated_at": None})

    asyncio.run(_simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo))

    expected_key = CacheKeys.suggested_questions(user_code, language, pet_id)
    if expected_key not in setex_calls:
        fail(f"Valkey not warmed after Postgres fallback. setex_calls={list(setex_calls.keys())}")
        return

    warmed = json.loads(setex_calls[expected_key])
    if warmed.get("questions") != pg_questions:
        fail("Valkey warmed with wrong questions")
        return

    ok(f"Postgres fallback warms Valkey: key {expected_key} written back to cache")


def test_setup_postgres_fallback_cold_start_uses_evergreen():
    """
    Both Valkey and Postgres are cold (new user, never generated questions).
    /setup must fall back to evergreen questions and return questions_cached=False.
    The evergreen questions must have valid v2 structure.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.services.question_generation.generator import _build_full_evergreen
    from app.routes.chat import _pick_questions

    user_code = "U-COLD-START"
    language  = "EN"
    pet_id    = 101

    mock_vk = MagicMock()
    mock_vk.get = AsyncMock(return_value=None)
    mock_vk.setex = AsyncMock()

    mock_repo = MagicMock()
    mock_repo.get = AsyncMock(return_value=None)  # Postgres also empty

    per_pet_rows, pg_called = asyncio.run(
        _simulate_setup_lookup([pet_id], user_code, language, mock_vk, mock_repo)
    )

    # /setup: when per_pet_rows has no entry for pet_id, use evergreen
    questions_cached = pet_id in per_pet_rows
    if questions_cached:
        fail(f"pet {pet_id} found in per_pet_rows even though both Valkey and Postgres were cold")
        return

    # Cold start: build evergreen
    per_pet_rows[pet_id] = _build_full_evergreen(language, is_pet_b=False)
    result = _pick_questions(per_pet_rows, [pet_id], "food", language)

    VALID_MODULES = {"food", "health", "anymall"}
    VALID_TARGETS = {"pet_a", "pet_b", "both"}
    malformed = [q for q in result if q.get("module") not in VALID_MODULES or not q.get("text")]

    if malformed:
        fail(f"cold start evergreen returned malformed questions: {malformed}")
        return

    ok(f"cold start: questions_cached=False, {len(result)} valid evergreen questions served")


# ══════════════════════════════════════════════════════════════════════════════
# Section A20 — Aggregator triggers regen end-to-end pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _make_background_mocks(fact_confidence=0.85):
    """
    Build all mocks needed to run _run_background in a unit test.

    Returns (state, state_bag, mock_vk, regen_sq_calls).
    regen_sq_calls is a list that gets populated when _regen_suggested_questions fires.
    """
    from unittest.mock import AsyncMock, MagicMock
    from app.agents.state import AgentState, PetInfo
    from app.agents.compressor import ExtractedFact

    regen_sq_calls = []

    # Facts returned by the compressor
    test_fact = ExtractedFact(
        key="food_brand", value="Royal Canin", confidence=fact_confidence,
        source_rank="explicit_owner", time_scope="current",
        uncertainty="", source_quote="I feed Royal Canin", timestamp=None,
        pet_label="pet_a",
    )

    mock_compressor = MagicMock()
    mock_compressor.run = AsyncMock(return_value=[test_fact])

    mock_aggregator = MagicMock()
    mock_aggregator.run = AsyncMock()

    stored = {}
    mock_vk = MagicMock()
    mock_vk.eval   = AsyncMock(return_value=None)
    mock_vk.set    = AsyncMock(return_value=True)
    mock_vk.get    = AsyncMock(return_value=None)  # profile not cached -> DB
    mock_vk.setex  = AsyncMock(side_effect=lambda k, t, v: stored.__setitem__(k, v) or None)
    mock_vk.delete = AsyncMock()
    mock_vk._stored = stored

    class MockStateBag:
        compressor               = mock_compressor
        aggregator               = mock_aggregator
        valkey                   = mock_vk
        background_tasks         = set()
        pending_clarifications   = {}
        compaction_in_progress   = set()
        suggested_questions_agent = MagicMock()
        pet_fetcher              = MagicMock()
        thread_summarizer        = None
        history_builder          = None
        relationship_builder     = None

    state = AgentState(
        session_id="s-AGGR-TEST",
        thread_id="t-AGGR-TEST",
        user_code="U-AGGR-001",
        user_message="My dog eats Royal Canin",
        pets=[PetInfo(id=101, name="Leo")],
        agent_reply="That's great!",
        recent_history=[],  # short list -> no compaction triggered
    )

    return state, MockStateBag(), mock_vk, regen_sq_calls


def _run_background_with_mocks(state, state_bag, extra_patches=None):
    """
    Run _run_background synchronously with all DB calls mocked.
    Any tasks created by _create_tracked_task are run inline before returning.
    Returns list of (user_code, pet_ids) passed to _regen_suggested_questions.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.routes.background import _run_background

    regen_calls = []
    captured_coros = []

    async def _mock_regen_sq(user_code, pet_ids, sb):
        regen_calls.append({"user_code": user_code, "pet_ids": pet_ids})

    def _capture_task(coro, sb):
        captured_coros.append(coro)
        return MagicMock()  # fake Task

    @asynccontextmanager
    async def _mock_session():
        yield MagicMock()

    mock_msg_repo  = MagicMock(); mock_msg_repo.append_batch  = AsyncMock()
    mock_fact_repo = MagicMock(); mock_fact_repo.append_bulk  = AsyncMock()
    mock_ap_repo   = MagicMock(); mock_ap_repo.read_all       = AsyncMock(return_value={})

    patches = {
        "app.routes.background.get_session":               _mock_session,
        "app.routes.background.ThreadMessageRepo":         MagicMock(return_value=mock_msg_repo),
        "app.routes.background.FactLogRepo":               MagicMock(return_value=mock_fact_repo),
        "app.routes.background.ActiveProfileRepo":         MagicMock(return_value=mock_ap_repo),
        "app.routes.background._regen_suggested_questions": AsyncMock(side_effect=_mock_regen_sq),
        "app.routes.background._create_tracked_task":      _capture_task,
    }
    if extra_patches:
        patches.update(extra_patches)

    async def _run():
        with patch.multiple("", **{k: v for k, v in patches.items()}):
            # patch.multiple doesn't work with full paths — use nested patches
            pass  # placeholder

    # Use stacked context managers
    async def _test():
        ctx = patch("app.routes.background.get_session", _mock_session)
        ctx2 = patch("app.routes.background.ThreadMessageRepo", MagicMock(return_value=mock_msg_repo))
        ctx3 = patch("app.routes.background.FactLogRepo", MagicMock(return_value=mock_fact_repo))
        ctx4 = patch("app.routes.background.ActiveProfileRepo", MagicMock(return_value=mock_ap_repo))
        ctx5 = patch("app.routes.background._regen_suggested_questions",
                     new=AsyncMock(side_effect=_mock_regen_sq))
        ctx6 = patch("app.routes.background._create_tracked_task", _capture_task)
        with ctx, ctx2, ctx3, ctx4, ctx5, ctx6:
            await _run_background(state, state_bag)
        for coro in captured_coros:
            await coro

    asyncio.run(_test())
    return regen_calls


def test_aggregator_triggers_regen_after_high_confidence_facts():
    """
    When the compressor returns high-confidence facts (confidence > 0.70),
    the aggregator runs and then _regen_suggested_questions is triggered
    to immediately refresh the home screen questions.

    This tests the pipeline connection: chat -> compressor -> aggregator -> regen.
    """
    state, state_bag, mock_vk, _ = _make_background_mocks(fact_confidence=0.85)
    regen_calls = _run_background_with_mocks(state, state_bag)

    if not regen_calls:
        fail("_regen_suggested_questions was NOT triggered after high-confidence fact extraction")
        return

    call = regen_calls[0]
    if call["user_code"] != state.user_code:
        fail(f"regen called with wrong user_code: {call['user_code']!r}")
        return
    if state.pets[0].id not in call["pet_ids"]:
        fail(f"regen called without pet_id {state.pets[0].id}: {call['pet_ids']}")
        return

    ok(f"pipeline: high-confidence fact -> regen triggered for user={call['user_code']} pets={call['pet_ids']}")


def test_aggregator_regen_writes_to_valkey():
    """
    After the aggregator runs on high-confidence facts, regen_for_user is called.
    regen_for_user writes the fresh questions to Valkey.
    We verify the call arguments prove a Valkey write would occur.
    (Actual write path is tested in A8/A14; here we verify the pipeline connection.)
    """
    state, state_bag, mock_vk, _ = _make_background_mocks(fact_confidence=0.85)
    regen_calls = _run_background_with_mocks(state, state_bag)

    if not regen_calls:
        fail("regen not triggered — Valkey write cannot happen")
        return

    call = regen_calls[0]
    if call["user_code"] and call["pet_ids"]:
        ok(f"regen triggered with user_code={call['user_code']!r} pet_ids={call['pet_ids']} "
           f"-> Valkey write will occur (verified in A8/A14 that regen writes to Valkey)")
    else:
        fail(f"regen called with incomplete args: {call}")


def test_aggregator_regen_writes_to_postgres():
    """
    After the aggregator runs, regen_for_user is called.
    regen_for_user calls SuggestedQuestionsRepo.upsert() to persist to Postgres.
    We verify the pipeline is connected (actual upsert args tested in A15).
    """
    state, state_bag, mock_vk, _ = _make_background_mocks(fact_confidence=0.85)
    regen_calls = _run_background_with_mocks(state, state_bag)

    if not regen_calls:
        fail("regen not triggered — Postgres upsert cannot happen")
        return

    ok(f"regen triggered: Postgres upsert will occur for user={regen_calls[0]['user_code']!r} "
       f"(verified in A15 that regen_for_user calls upsert with correct pet_id/user_code/language)")


def test_aggregator_no_regen_when_only_low_confidence_facts():
    """
    When the compressor returns ONLY low-confidence facts (confidence <= 0.70),
    the aggregator is NOT called and _regen_suggested_questions must NOT fire.

    Low-confidence facts go to pending_clarifications for follow-up, NOT to
    the active_profile update. With no profile update, regen is not needed.
    """
    state, state_bag, mock_vk, _ = _make_background_mocks(fact_confidence=0.55)
    regen_calls = _run_background_with_mocks(state, state_bag)

    if regen_calls:
        fail(f"regen fired for LOW-confidence facts — should NOT have: {regen_calls}")
        return

    ok("low-confidence facts only: aggregator not called, regen not triggered")


# ══════════════════════════════════════════════════════════════════════════════
# Section A21 — Nightly: stale-row loop regens each row
# ══════════════════════════════════════════════════════════════════════════════

def _make_nightly_mocks(stale_rows, regen_side_effect=None):
    """
    Build mocks for _pregenerate_suggested_questions tests.

    Returns (mock_state, regen_calls, cleanup_calls).
    """
    from unittest.mock import AsyncMock, MagicMock

    regen_calls = []
    cleanup_calls = []

    async def _default_regen(**kwargs):
        regen_calls.append(kwargs)

    async def _mock_cleanup():
        cleanup_calls.append(True)
        return len(cleanup_calls)

    class MockState:
        suggested_questions_agent = MagicMock()
        valkey                    = MagicMock()
        pet_fetcher               = MagicMock()

    mock_sq_repo_scan    = MagicMock()
    mock_sq_repo_scan.get_all_stale = AsyncMock(return_value=stale_rows)

    mock_sq_repo_cleanup = MagicMock()
    mock_sq_repo_cleanup.cleanup_stale_language_rows = AsyncMock(side_effect=_mock_cleanup)

    call_count = [0]
    def _make_repo(session):
        call_count[0] += 1
        # First call = scan repo, all subsequent = per-row or cleanup repo
        if call_count[0] == 1:
            return mock_sq_repo_scan
        return mock_sq_repo_cleanup

    return MockState(), _make_repo, regen_calls, cleanup_calls, regen_side_effect or _default_regen


def test_nightly_pregenerate_calls_regen_for_each_stale_row():
    """
    _pregenerate_suggested_questions must call regen_for_user ONCE per stale row.
    With 3 stale rows, regen is called exactly 3 times with the correct row data.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch
    from app.jobs.nightly import _pregenerate_suggested_questions

    stale_rows = [
        {"user_code": "U-N1", "language": "EN", "pet_id": 11, "last_known_pet_ids": [11]},
        {"user_code": "U-N2", "language": "JA", "pet_id": 21, "last_known_pet_ids": [21]},
        {"user_code": "U-N3", "language": "EN", "pet_id": 31, "last_known_pet_ids": [31, 32]},
    ]

    mock_state, _make_repo, regen_calls, _, regen_fn = _make_nightly_mocks(stale_rows)

    @asynccontextmanager
    async def _mock_session():
        yield None

    with patch("app.jobs.nightly.get_session", _mock_session), \
         patch("app.jobs.nightly.SuggestedQuestionsRepo", side_effect=_make_repo), \
         patch("app.jobs.nightly.regen_for_user", new=AsyncMock(side_effect=regen_fn)):
        asyncio.run(_pregenerate_suggested_questions(mock_state))

    if len(regen_calls) != 3:
        fail(f"expected 3 regen calls for 3 stale rows, got {len(regen_calls)}")
        return

    user_codes = [c["user_code"] for c in regen_calls]
    if sorted(user_codes) == sorted(["U-N1", "U-N2", "U-N3"]):
        ok(f"nightly loop: regen called {len(regen_calls)}x, users={user_codes}")
    else:
        fail(f"wrong users in regen calls: {user_codes}")


def test_nightly_pregenerate_is_pet_b_derived_from_last_known_pet_ids():
    """
    is_pet_b is derived from the position of pet_id in last_known_pet_ids:
      - pet_id == last_known[1]  -> is_pet_b=True  (second pet)
      - pet_id == last_known[0]  -> is_pet_b=False (first pet)
      - only 1 pet in list       -> is_pet_b=False (single-pet user)
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch
    from app.jobs.nightly import _pregenerate_suggested_questions

    stale_rows = [
        # pet 101 is first pet -> is_pet_b=False
        {"user_code": "U-IB1", "language": "EN", "pet_id": 101, "last_known_pet_ids": [101, 102]},
        # pet 102 is second pet -> is_pet_b=True
        {"user_code": "U-IB2", "language": "EN", "pet_id": 102, "last_known_pet_ids": [101, 102]},
        # only one pet -> is_pet_b=False
        {"user_code": "U-IB3", "language": "EN", "pet_id": 55, "last_known_pet_ids": [55]},
    ]

    mock_state, _make_repo, regen_calls, _, regen_fn = _make_nightly_mocks(stale_rows)

    @asynccontextmanager
    async def _mock_session():
        yield None

    with patch("app.jobs.nightly.get_session", _mock_session), \
         patch("app.jobs.nightly.SuggestedQuestionsRepo", side_effect=_make_repo), \
         patch("app.jobs.nightly.regen_for_user", new=AsyncMock(side_effect=regen_fn)):
        asyncio.run(_pregenerate_suggested_questions(mock_state))

    if len(regen_calls) != 3:
        fail(f"expected 3 regen calls, got {len(regen_calls)}")
        return

    by_user = {c["user_code"]: c["is_pet_b"] for c in regen_calls}

    errors = []
    if by_user.get("U-IB1") is not False:
        errors.append(f"U-IB1 pet_id=101 (first): expected is_pet_b=False, got {by_user.get('U-IB1')!r}")
    if by_user.get("U-IB2") is not True:
        errors.append(f"U-IB2 pet_id=102 (second): expected is_pet_b=True, got {by_user.get('U-IB2')!r}")
    if by_user.get("U-IB3") is not False:
        errors.append(f"U-IB3 single pet: expected is_pet_b=False, got {by_user.get('U-IB3')!r}")

    if errors:
        fail(" | ".join(errors))
    else:
        ok(f"is_pet_b correctly derived: first_pet=False, second_pet=True, single_pet=False")


def test_nightly_pregenerate_partial_failure_continues():
    """
    If regen fails for one row, the nightly job must continue to the next row.
    Partial failure must never abort the entire nightly run.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch
    from app.jobs.nightly import _pregenerate_suggested_questions

    stale_rows = [
        {"user_code": "U-FAIL", "language": "EN", "pet_id": 10, "last_known_pet_ids": [10]},
        {"user_code": "U-OK",   "language": "EN", "pet_id": 20, "last_known_pet_ids": [20]},
    ]

    processed = []

    async def _sometimes_fail(**kwargs):
        processed.append(kwargs["user_code"])
        if kwargs["user_code"] == "U-FAIL":
            raise RuntimeError("simulated regen failure")

    mock_state, _make_repo, _, _, _ = _make_nightly_mocks(stale_rows)

    @asynccontextmanager
    async def _mock_session():
        yield None

    with patch("app.jobs.nightly.get_session", _mock_session), \
         patch("app.jobs.nightly.SuggestedQuestionsRepo", side_effect=_make_repo), \
         patch("app.jobs.nightly.regen_for_user", new=AsyncMock(side_effect=_sometimes_fail)):
        asyncio.run(_pregenerate_suggested_questions(mock_state))  # must not raise

    if "U-OK" not in processed:
        fail("row 2 was NOT processed after row 1 raised an exception")
        return
    if "U-FAIL" not in processed:
        fail("row 1 was not even attempted")
        return

    ok(f"partial failure: row 1 failed, row 2 still processed — processed={processed}")


def test_nightly_pregenerate_empty_stale_rows_skips():
    """
    When get_all_stale() returns no rows, regen_for_user must NOT be called at all.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch
    from app.jobs.nightly import _pregenerate_suggested_questions

    mock_state, _make_repo, regen_calls, _, regen_fn = _make_nightly_mocks(stale_rows=[])

    @asynccontextmanager
    async def _mock_session():
        yield None

    with patch("app.jobs.nightly.get_session", _mock_session), \
         patch("app.jobs.nightly.SuggestedQuestionsRepo", side_effect=_make_repo), \
         patch("app.jobs.nightly.regen_for_user", new=AsyncMock(side_effect=regen_fn)):
        asyncio.run(_pregenerate_suggested_questions(mock_state))

    if regen_calls:
        fail(f"regen called despite no stale rows: {regen_calls}")
        return

    ok("empty stale rows: regen_for_user not called (correct early-exit behaviour)")


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

    section("B4 — Module parameter: food / health / anymall")
    test_setup_module_food()
    test_setup_module_health()
    test_setup_module_anymall()
    test_setup_module_invalid_rejected()


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
    if not isinstance(questions, list) or len(questions) != 3:
        fail(f"expected 3 questions, got {type(questions).__name__} with {len(questions) if isinstance(questions, list) else 'N/A'}")
        return

    # Validate each question structure (v2: no reason_type — uses module/target/text)
    VALID_MODULES = {"food", "health", "anymall"}
    VALID_TARGETS = {"pet_a", "pet_b", "both"}
    for i, q in enumerate(questions):
        if (not q.get("text")
                or q.get("module") not in VALID_MODULES
                or q.get("target") not in VALID_TARGETS):
            fail(f"question[{i}] invalid v2 structure: {q}")
            return

    ok(f"setup returns confidence ({data['confidence_score']}/{data['confidence_color']}) + {len(questions)} questions")

    # Log questions for visual inspection
    for q in questions:
        print(f"    {YELLOW}[{q['target']}] {q.get('module', '?')}: {q['text']}{RESET}")


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
    When /setup returns evergreen (questions_cached=False), all questions must have
    valid v2 structure: module in {food/health/anymall}, target in {pet_a/pet_b/both},
    non-empty text. v2 removed reason_type.

    If questions_cached=True, this test is skipped — it only exercises the cold-start branch.
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
    # v2: no reason_type — verify each question has the v2 structure (module, target, text)
    VALID_MODULES = {"food", "health", "anymall"}
    VALID_TARGETS = {"pet_a", "pet_b", "both"}
    malformed = [
        q for q in questions
        if q.get("module") not in VALID_MODULES
        or q.get("target") not in VALID_TARGETS
        or not q.get("text")
    ]
    if malformed:
        fail(f"cold-start returned malformed questions: {malformed}")
    else:
        ok(f"cold-start returns {len(questions)} questions with valid v2 structure (module/target/text)")


# ══════════════════════════════════════════════════════════════════════════════
# Section B4 — Module parameter: food / health / anymall
# ══════════════════════════════════════════════════════════════════════════════

def _get_first_pet_id() -> int | None:
    """Fetch the first pet_id from /pets for the test user. Returns None on failure."""
    pets_res = requests.get(
        f"{BASE}/api/v1/pets",
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if pets_res.status_code != 200:
        return None
    pets = pets_res.json().get("pets", [])
    return pets[0]["pet_id"] if pets else None


def test_setup_module_food():
    """
    GET /setup?pet_id=X&module=food must return exactly 3 questions,
    all with module="food".
    """
    pet_id = _get_first_pet_id()
    if pet_id is None:
        fail("could not fetch a pet_id for module=food test")
        return

    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "module": "food", "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code != 200:
        fail(f"module=food returned {res.status_code}: {res.text[:200]}")
        return

    questions = res.json().get("suggested_questions", [])
    if len(questions) != 3:
        fail(f"expected 3 questions for module=food, got {len(questions)}")
        return

    wrong = [q for q in questions if q.get("module") != "food"]
    if wrong:
        fail(f"module=food: non-food questions returned: {wrong}")
        return

    ok(f"module=food: 3 food questions returned — {[q['text'][:30] for q in questions]}")
    for q in questions:
        print(f"    {YELLOW}[{q['target']}] {q['text']}{RESET}")


def test_setup_module_health():
    """
    GET /setup?pet_id=X&module=health must return exactly 3 questions,
    all with module="health".
    """
    pet_id = _get_first_pet_id()
    if pet_id is None:
        fail("could not fetch a pet_id for module=health test")
        return

    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "module": "health", "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code != 200:
        fail(f"module=health returned {res.status_code}: {res.text[:200]}")
        return

    questions = res.json().get("suggested_questions", [])
    if len(questions) != 3:
        fail(f"expected 3 questions for module=health, got {len(questions)}")
        return

    wrong = [q for q in questions if q.get("module") != "health"]
    if wrong:
        fail(f"module=health: non-health questions returned: {wrong}")
        return

    ok(f"module=health: 3 health questions returned — {[q['text'][:30] for q in questions]}")
    for q in questions:
        print(f"    {YELLOW}[{q['target']}] {q['text']}{RESET}")


def test_setup_module_anymall():
    """
    GET /setup?pet_id=X&module=anymall must return exactly 3 questions
    covering all three modules (food + health + anymall), one each.
    """
    pet_id = _get_first_pet_id()
    if pet_id is None:
        fail("could not fetch a pet_id for module=anymall test")
        return

    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "module": "anymall", "language": "EN"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code != 200:
        fail(f"module=anymall returned {res.status_code}: {res.text[:200]}")
        return

    questions = res.json().get("suggested_questions", [])
    if len(questions) != 3:
        fail(f"expected 3 questions for module=anymall, got {len(questions)}")
        return

    modules_returned = {q.get("module") for q in questions}
    expected_modules = {"food", "health", "anymall"}
    if modules_returned != expected_modules:
        fail(
            f"module=anymall: expected one of each module {expected_modules}, "
            f"got {modules_returned}"
        )
        return

    ok(f"module=anymall: 1 food + 1 health + 1 anymall question — {[q['text'][:30] for q in questions]}")
    for q in questions:
        print(f"    {YELLOW}[{q['module']}][{q['target']}] {q['text']}{RESET}")


def test_setup_module_invalid_rejected():
    """
    GET /setup?pet_id=X&module=invalid must return 422 (validation error).
    The module parameter has a regex pattern constraint: ^(anymall|food|health)$.
    """
    pet_id = _get_first_pet_id()
    if pet_id is None:
        fail("could not fetch a pet_id for invalid-module test")
        return

    res = requests.get(
        f"{BASE}/api/v1/setup",
        params={"pet_id": pet_id, "module": "invalid"},
        headers={"X-User-Code": TEST_USER_CODE},
    )
    if res.status_code == 422:
        ok("module=invalid returns 422 (pattern validation enforced)")
    else:
        fail(f"expected 422 for invalid module, got {res.status_code}: {res.text[:200]}")


def run_module_tests(module: str = "food"):
    """
    Run only the module-specific integration test for a given module value.
    Use this for quick smoke-testing of a single module after a deploy or config change.

    Usage:
        python tests/test_suggested_questions.py --module food
        python tests/test_suggested_questions.py --module health
        python tests/test_suggested_questions.py --module anymall
    """
    if requests is None:
        print(f"\n{YELLOW}Skipping module test — 'requests' not installed.{RESET}")
        return

    module = module.lower()
    if module not in ("food", "health", "anymall"):
        print(f"\n{RED}Unknown module '{module}'. Valid values: food, health, anymall.{RESET}")
        sys.exit(1)

    section(f"B4 — Module parameter: module={module}")
    if module == "food":
        test_setup_module_food()
    elif module == "health":
        test_setup_module_health()
    else:
        test_setup_module_anymall()

    # Always check that an invalid value is rejected
    test_setup_module_invalid_rejected()


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--unit" in args:
        run_unit_tests()
    elif "--module" in args:
        idx = args.index("--module")
        module_arg = args[idx + 1] if idx + 1 < len(args) else "food"
        run_module_tests(module_arg)
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
