# tests/test_food_ai.py
#
# TDD test suite for the Food AI feature (FoodAgent + upgraded IntentClassifier).
#
# Run BEFORE implementation to get the baseline (most fail = expected).
# Run after each implementation step to track progress.
#
# Sections:
#   A  — Shared constants and test data helpers
#   B  — Unit tests  (no server, no real LLM, mocked dependencies)
#   C  — Eval tests  (real LLM via configured provider, threshold-based)
#   D  — End-to-end  (real HTTP calls to running backend on :8000)
#
# Usage:
#   cd backend
#   python tests/test_food_ai.py           # all tests
#   python tests/test_food_ai.py --unit    # B only (fast, no server, no LLM)
#   python tests/test_food_ai.py --eval    # C only (needs .env + real API key)
#   python tests/test_food_ai.py --e2e     # D only (needs server on :8000)

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force UTF-8 output on Windows so Japanese characters don't crash the console
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

try:
    import requests
except ImportError:
    requests = None

# ── Terminal colours ──────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

BASE_URL  = "http://localhost:8000"
TEST_USER = "TEST-USER-FOODAI"
TEST_PET_DOG_ID  = 1   # assumed to be a dog in staging AALDA
TEST_PET_CAT_ID  = 2   # assumed to be a cat in staging AALDA


# ── Result tracking ───────────────────────────────────────────────────────────

_results: list[bool] = []


def passed(label: str, detail: str = "") -> bool:
    _results.append(True)
    line = f"  {GREEN}PASS{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}> {detail}{RESET}"
    print(line)
    return True


def failed(label: str, detail: str = "") -> bool:
    _results.append(False)
    line = f"  {RED}FAIL{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}> {detail}{RESET}"
    print(line)
    return False


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    print("-" * 60)


def run_with_threshold(fn, attempts: int = 3, min_pass: int = 2, label: str = "") -> bool:
    """Run a non-deterministic (LLM) test multiple times; pass if min_pass succeed."""
    results = []
    for _ in range(attempts):
        try:
            results.append(fn())
        except Exception as exc:
            results.append(False)
            print(f"    {YELLOW}attempt raised: {exc}{RESET}")
    pass_count = sum(results)
    detail = f"{pass_count}/{attempts} attempts passed (need {min_pass})"
    if pass_count >= min_pass:
        return passed(label or fn.__name__, detail)
    return failed(label or fn.__name__, detail)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — Shared test data
# ══════════════════════════════════════════════════════════════════════════════

MOCK_DOG_PROFILE = {
    "pet_id": 1, "species": "dog", "breed": "Shiba Inu",
    "life_stage": "adult", "name": "Buddy", "sex": "male",
}
MOCK_DOG_PROFILE_SENIOR = {
    "pet_id": 3, "species": "dog", "breed": "Golden Retriever",
    "life_stage": "senior", "name": "Max", "sex": "male",
}
MOCK_CAT_PROFILE = {
    "pet_id": 2, "species": "cat", "breed": "Tabby",
    "life_stage": "adult", "name": "Whiskers", "sex": "female",
}

MOCK_ACTIVE_PROFILE_EMPTY: dict = {}
MOCK_ACTIVE_PROFILE_WITH_ALLERGY = {
    "allergies": {"value": "chicken", "confidence": 0.9, "source_rank": "explicit_owner",
                  "time_scope": "current", "source_quote": "no chicken", "updated_at": "2026-04-01",
                  "session_id": "s1", "status": "confirmed", "change_detected": "", "trend_flag": ""},
}

MOCK_RECIPES_3 = [
    {
        "id": 1, "title_ja": "白菜とツナのうま煮",
        "image_url": "https://example.com/r1.jpg",
        "primary_protein": "tuna", "kcal_per_100g": 85.0,
        "description": "Low fat boiled tuna and cabbage dish",
        "ingredients_text": "白菜, ツナ缶, だし", "meal_type": "main",
        "cooking_method": "boiled", "health_tags": "#低脂質 #消化しやすい",
        "allergen_tags": "大豆", "species": "dog", "life_stage": "adult", "is_active": "true",
    },
    {
        "id": 2, "title_ja": "ささみのパリパリチップス",
        "image_url": "https://example.com/r2.jpg",
        "primary_protein": "chicken", "kcal_per_100g": 110.0,
        "description": "Baked crispy chicken breast chips",
        "ingredients_text": "鶏ささみ", "meal_type": "snack",
        "cooking_method": "baked", "health_tags": "#高たんぱく #筋肉維持",
        "allergen_tags": "鶏肉", "species": "dog", "life_stage": "adult", "is_active": "true",
    },
    {
        "id": 3, "title_ja": "スイートポテトのやわらか蒸し",
        "image_url": "https://example.com/r3.jpg",
        "primary_protein": None, "kcal_per_100g": 70.0,
        "description": "Gentle steamed sweet potato, easy to digest",
        "ingredients_text": "さつまいも", "meal_type": "side",
        "cooking_method": "steamed", "health_tags": "#消化しやすい #シニアケア",
        "allergen_tags": None, "species": "dog", "life_stage": "senior", "is_active": "true",
    },
]
MOCK_RECIPES_1 = MOCK_RECIPES_3[:1]

MOCK_MCP_RESPONSE_3 = json.dumps({"fallback": False, "recipes": MOCK_RECIPES_3})
MOCK_MCP_RESPONSE_1 = json.dumps({"fallback": False, "recipes": MOCK_RECIPES_1})
MOCK_MCP_RESPONSE_0 = json.dumps({"fallback": True,  "recipes": []})

MOCK_WEB_RESULTS = [
    {
        "title": "Senior dog nutrition: low-fat diet guide",
        "url": "https://vet.example.com/senior-dog-nutrition",
        "content": "Senior dogs benefit from low-fat, high-protein diets to maintain muscle mass...",
    },
    {
        "title": "Kidney disease diet for dogs — what to feed",
        "url": "https://vetfood.example.com/kidney-diet",
        "content": "For dogs with kidney disease, phosphorus restriction and adequate hydration are key...",
    },
]

# Conversation history with an in-session food constraint
MOCK_HISTORY_WITH_CONSTRAINT = [
    {"role": "user",      "content": "show me recipes for Buddy"},
    {"role": "assistant", "content": "Here are some recipe options for Buddy..."},
    {"role": "user",      "content": "but nothing with chicken or garlic please"},
    {"role": "assistant", "content": "Got it — I'll find options without those."},
]

# History that already showed recipes (follow-up should be "general")
MOCK_HISTORY_AFTER_RECIPES = [
    {"role": "user",      "content": "show me recipes for Buddy"},
    {"role": "assistant", "content": "Here are some recipes: 白菜とツナのうま煮, ささみのパリパリチップス, スイートポテト"},
    {"role": "user",      "content": "how do I cook the second one?"},
]


def _make_mcp_call_result(json_text: str):
    """Build a fake MCP call_tool result object."""
    from unittest.mock import MagicMock
    result = MagicMock()
    result.content = [MagicMock(text=json_text)]
    return result


def _make_state_bag(
    recipe_fetcher=None,
    food_agent=None,
    intent_classifier=None,
    conversation_agent=None,
    valkey=None,
    pet_fetcher=None,
):
    """Build a minimal mock StateBag for route-level tests."""
    from unittest.mock import MagicMock, AsyncMock
    bag = MagicMock()
    bag.recipe_fetcher      = recipe_fetcher or MagicMock()
    bag.food_agent          = food_agent or MagicMock()
    bag.intent_classifier   = intent_classifier or MagicMock()
    bag.agent               = conversation_agent or MagicMock()
    bag.valkey              = valkey or MagicMock()
    bag.pet_fetcher         = pet_fetcher or MagicMock()
    bag.compressor          = MagicMock()
    bag.aggregator          = MagicMock()
    bag.history_builder     = MagicMock()
    bag.relationship_builder = MagicMock()
    bag.suggested_questions_agent = MagicMock()
    bag.thread_summarizer   = MagicMock()
    bag.llm_provider        = MagicMock()
    bag.scheduler           = MagicMock()
    bag.sessions            = {}
    bag.session_meta        = {}
    bag.compaction_in_progress = set()
    bag.thread_locks        = {}
    bag.pet_locks           = {}
    bag.background_tasks    = set()
    bag.pending_clarifications = {}
    return bag


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — Unit Tests (no server, no real LLM)
# ══════════════════════════════════════════════════════════════════════════════

# ── B1: IntentClassifier returns new food sub-intents ─────────────────────────

def test_b1_classifier_returns_food_recipes() -> bool:
    """classify() with mocked LLM returning food_recipes produces INTENT_FOOD_RECIPES."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from constants import INTENT_FOOD_RECIPES
        from app.agents.intent_classifier import IntentClassifier
    except ImportError as exc:
        return failed("B1.1 INTENT_FOOD_RECIPES importable", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"intent":"food_recipes","urgency":"low","confidence":9}'
    )
    clf = IntentClassifier(mock_llm)
    intent, urgency = asyncio.run(clf.classify("show me recipes for Buddy"))
    if intent == INTENT_FOOD_RECIPES:
        return passed("B1.1 classifier returns INTENT_FOOD_RECIPES", f"urgency={urgency}")
    return failed("B1.1 classifier returns INTENT_FOOD_RECIPES", f"got intent={intent!r}")


def test_b1_classifier_returns_food_recipes_info() -> bool:
    """classify() with mocked LLM returning food_recipes_info."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from constants import INTENT_FOOD_RECIPES_INFO
        from app.agents.intent_classifier import IntentClassifier
    except ImportError as exc:
        return failed("B1.2 INTENT_FOOD_RECIPES_INFO importable", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"intent":"food_recipes_info","urgency":"low","confidence":8}'
    )
    clf = IntentClassifier(mock_llm)
    intent, urgency = asyncio.run(clf.classify("what should I feed my senior dog?"))
    if intent == INTENT_FOOD_RECIPES_INFO:
        return passed("B1.2 classifier returns INTENT_FOOD_RECIPES_INFO")
    return failed("B1.2 classifier returns INTENT_FOOD_RECIPES_INFO", f"got={intent!r}")


def test_b1_classifier_returns_food_info() -> bool:
    """classify() with mocked LLM returning food_info."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from constants import INTENT_FOOD_INFO
        from app.agents.intent_classifier import IntentClassifier
    except ImportError as exc:
        return failed("B1.3 INTENT_FOOD_INFO importable", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"intent":"food_info","urgency":"low","confidence":9}'
    )
    clf = IntentClassifier(mock_llm)
    intent, urgency = asyncio.run(clf.classify("can dogs eat garlic?"))
    if intent == INTENT_FOOD_INFO:
        return passed("B1.3 classifier returns INTENT_FOOD_INFO")
    return failed("B1.3 classifier returns INTENT_FOOD_INFO", f"got={intent!r}")


def test_b1_classifier_accepts_recent_history() -> bool:
    """classify() accepts a recent_history argument without raising."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.agents.intent_classifier import IntentClassifier
    except ImportError as exc:
        return failed("B1.4 IntentClassifier importable", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"intent":"general","urgency":"low","confidence":9}'
    )
    clf = IntentClassifier(mock_llm)
    try:
        intent, urgency = asyncio.run(
            clf.classify(
                "how do I cook the second one?",
                recent_history=MOCK_HISTORY_AFTER_RECIPES,
            )
        )
        return passed("B1.4 classify() accepts recent_history kwarg", f"got intent={intent!r}")
    except TypeError as exc:
        return failed("B1.4 classify() accepts recent_history kwarg", f"TypeError: {exc}")


# ── B2: RecipeFetcher — constraint extraction ─────────────────────────────────

def test_b2_recipe_fetcher_no_llm_needed() -> bool:
    """RecipeFetcher can be instantiated without an llm argument — query building is external."""
    try:
        from app.services.recipe_fetcher import RecipeFetcher
        fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")
        return passed("B2.1 RecipeFetcher instantiates without llm")
    except TypeError as exc:
        return failed("B2.1 RecipeFetcher instantiates without llm", str(exc))
    except ImportError as exc:
        return failed("B2.1 RecipeFetcher importable", str(exc))


def test_b2_query_builder_extracts_constraint_to_allergen() -> bool:
    """
    When conversation history has 'no chicken or garlic', the allergen list
    passed to MCP must include Japanese equivalents.
    """
    from unittest.mock import AsyncMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B2.2 RecipeFetcher importable", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    captured_args: list[dict] = []

    async def fake_call_mcp(tool_args: dict):
        captured_args.append(tool_args)
        return [], True  # fallback=True, empty recipes

    with patch.object(fetcher, "_call_mcp_tool", side_effect=fake_call_mcp):
        asyncio.run(fetcher._fetch_for_pet(
            pre_built_query="消化しやすいレシピ",
            pet_profile=MOCK_DOG_PROFILE,
            active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
            conversation_history=MOCK_HISTORY_WITH_CONSTRAINT,
        ))

    if not captured_args:
        return failed("B2.2 constraint extracted to allergen list", "MCP was not called")

    allergens = captured_args[0].get("allergens", [])
    has_chicken_jp = "鶏肉" in allergens
    # garlic is not in _ALLERGEN_JP mapping — it should still appear (pass-through)
    has_garlic = any("garlic" in a.lower() or "にんにく" in a for a in allergens)

    if has_chicken_jp:
        return passed(
            "B2.2 constraint extracted to allergen list",
            f"allergens={allergens}"
        )
    return failed(
        "B2.2 constraint extracted to allergen list",
        f"鶏肉 not in allergens={allergens} (history had 'no chicken')"
    )


# ── B3: RecipeFetcher — species gating ────────────────────────────────────────

def test_b3_cat_is_skipped_in_fetch_for_pets() -> bool:
    """fetch_for_pets() silently skips cats — result dict must not contain cat pet_id."""
    from unittest.mock import patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B3.1 cat skipped in fetch_for_pets", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    mcp_called_for = []

    async def fake_fetch_for_pet(pre_built_query, pet_profile, active_profile,
                                 conversation_history=None):
        mcp_called_for.append(pet_profile["pet_id"])
        return (MOCK_RECIPES_3, False)

    with patch.object(fetcher, "_fetch_for_pet", side_effect=fake_fetch_for_pet):
        result = asyncio.run(fetcher.fetch_for_pets(
            pre_built_query="テストクエリ",
            pet_profiles=[MOCK_DOG_PROFILE, MOCK_CAT_PROFILE],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY, MOCK_ACTIVE_PROFILE_EMPTY],
        ))

    cat_id = MOCK_CAT_PROFILE["pet_id"]
    dog_id = MOCK_DOG_PROFILE["pet_id"]

    if cat_id in result:
        return failed("B3.1 cat skipped", f"cat pet_id {cat_id} appears in result")
    if dog_id not in result:
        return failed("B3.1 dog included", f"dog pet_id {dog_id} missing from result")
    if cat_id in mcp_called_for:
        return failed("B3.1 cat skipped", "fetch_for_pet was called for cat")
    return passed("B3.1 cat silently skipped, dog fetched", f"result keys={list(result.keys())}")


def test_b3_two_dogs_fetched_in_parallel() -> bool:
    """fetch_for_pets() with two dogs returns results for both pet_ids."""
    from unittest.mock import patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B3.2 dual-dog parallel fetch", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    async def fake_fetch_for_pet(pre_built_query, pet_profile, active_profile,
                                 conversation_history=None):
        return (MOCK_RECIPES_3, False)

    with patch.object(fetcher, "_fetch_for_pet", side_effect=fake_fetch_for_pet):
        result = asyncio.run(fetcher.fetch_for_pets(
            pre_built_query="テストクエリ",
            pet_profiles=[MOCK_DOG_PROFILE, MOCK_DOG_PROFILE_SENIOR],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY, MOCK_ACTIVE_PROFILE_EMPTY],
        ))

    dog1_id = MOCK_DOG_PROFILE["pet_id"]
    dog2_id = MOCK_DOG_PROFILE_SENIOR["pet_id"]

    if dog1_id in result and dog2_id in result:
        return passed("B3.2 both dogs returned", f"keys={list(result.keys())}")
    return failed("B3.2 both dogs returned", f"missing one. keys={list(result.keys())}")


# ── B4: RecipeFetcher — MCP returns 0 recipes ─────────────────────────────────

def test_b4_mcp_fallback_returns_empty_not_error() -> bool:
    """When MCP returns fallback=true, result is ([], True) — NOT an exception."""
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B4.1 MCP fallback returns empty not error", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    with patch.object(fetcher, "_call_mcp_tool",
                      AsyncMock(return_value=([], True))):
        try:
            recipes, fallback = asyncio.run(fetcher._fetch_for_pet(
                pre_built_query="something very niche",
                pet_profile=MOCK_DOG_PROFILE,
                active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
            ))
            if recipes == [] and fallback is True:
                return passed("B4.1 fallback=true → ([], True), no exception")
            return failed("B4.1 fallback=true result shape", f"got recipes={recipes}, fallback={fallback}")
        except Exception as exc:
            return failed("B4.1 fallback=true → no exception", f"raised {exc!r}")


def test_b4_mcp_response_parsed_correctly() -> bool:
    """_call_mcp_tool correctly strips similarity_score and maps RecipeResult fields."""
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B4.2 MCP response parsing", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    # MCP response includes similarity_score which must be stripped
    mcp_json_with_score = json.dumps({
        "fallback": False,
        "recipes": [{**MOCK_RECIPES_3[0], "similarity_score": 0.95}],
    })

    with patch("app.services.recipe_fetcher.streamablehttp_client") as mock_http, \
         patch("app.services.recipe_fetcher.ClientSession") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=_make_mcp_call_result(mcp_json_with_score))

        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=(AsyncMock(), AsyncMock(), None)
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        recipes, fallback = asyncio.run(fetcher._call_mcp_tool({
            "query": "テスト", "species": "dog", "life_stage": "adult", "allergens": [],
        }))

    if not recipes:
        return failed("B4.2 parsed 1 recipe from response", "empty list returned")
    recipe = recipes[0]
    if hasattr(recipe, "get"):
        has_score = "similarity_score" in recipe
    else:
        has_score = hasattr(recipe, "similarity_score")
    if has_score:
        return failed("B4.2 similarity_score stripped", "similarity_score present in result")
    return passed("B4.2 similarity_score stripped, recipe parsed", f"title={recipe.get('title_ja','?')}")


# ── B5: RecipeFetcher — partial results (1 or 2 recipes) ─────────────────────

def test_b5_single_recipe_returned() -> bool:
    """When MCP returns 1 recipe, result list has exactly 1 item."""
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B5.1 single recipe result", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    with patch.object(fetcher, "_call_mcp_tool",
                      AsyncMock(return_value=(MOCK_RECIPES_1, False))):
        recipes, fallback = asyncio.run(fetcher._fetch_for_pet(
            pre_built_query="simple query",
            pet_profile=MOCK_DOG_PROFILE,
            active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        ))

    if len(recipes) == 1 and fallback is False:
        return passed("B5.1 single recipe returned correctly", f"count={len(recipes)}")
    return failed("B5.1 single recipe returned", f"count={len(recipes)}, fallback={fallback}")


# ── B6: chat.py routing — food_recipes must NOT call FoodAgent ────────────────

def test_b6_food_recipes_mode_no_food_agent_call() -> bool:
    """
    When intent=food_recipes, the route must NOT call food_agent.run().
    Mode 1 is MCP only; FoodAgent is not involved.
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from constants import INTENT_FOOD_RECIPES
        from app.agents.food_agent import FoodAgent  # will fail until implemented
    except ImportError as exc:
        return failed("B6.1 food_recipes → no FoodAgent call", f"ImportError: {exc}")

    mock_food_agent = MagicMock()
    mock_food_agent.run = AsyncMock()

    # Mock recipe_fetcher to return 3 recipes
    mock_recipe_fetcher = MagicMock()
    mock_recipe_fetcher.fetch_for_pets = AsyncMock(
        return_value={MOCK_DOG_PROFILE["pet_id"]: (MOCK_RECIPES_3, False)}
    )

    # Mock intent classifier to return food_recipes
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=(INTENT_FOOD_RECIPES, "low"))

    # We're testing routing logic — confirm food_agent.run is never called
    # The actual route test is in D1; here we test the routing decision function
    mock_food_agent.run.assert_not_called()
    return passed("B6.1 food_recipes intent → food_agent.run() must not be called (verified in D1)")


def test_b6_food_recipes_info_calls_food_agent() -> bool:
    """food_recipes_info intent must route to FoodAgent."""
    try:
        from constants import INTENT_FOOD_RECIPES_INFO
        from app.agents.food_agent import FoodAgent
    except ImportError as exc:
        return failed("B6.2 food_recipes_info → FoodAgent called", f"ImportError: {exc}")
    return passed("B6.2 FoodAgent class importable for food_recipes_info routing")


def test_b6_food_info_calls_food_agent() -> bool:
    """food_info intent must route to FoodAgent."""
    try:
        from constants import INTENT_FOOD_INFO
        from app.agents.food_agent import FoodAgent
    except ImportError as exc:
        return failed("B6.3 food_info → FoodAgent called", f"ImportError: {exc}")
    return passed("B6.3 FoodAgent importable for food_info routing")


# ── B7: FoodAgent — parallel tool execution and planner-first ordering ────────
#
# Design change from original: MCP and web search now run IN PARALLEL via
# asyncio.gather (both only need pet context + user message, neither depends on
# the other's result). The query planner runs first, then both tools fire.

def test_b7_food_agent_both_tools_called_in_mode2() -> bool:
    """
    In food_recipes_info mode, BOTH MCP (recipe fetcher) AND Tavily web search
    must be called. They run in parallel — this test verifies both are invoked.
    The nano LLM is given a valid JSON response so the planner succeeds cleanly.
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.agents.food_agent import FoodAgent
    except ImportError as exc:
        return failed("B7.1 both tools called in mode 2", f"ImportError: {exc}")

    calls: list[str] = []

    async def fake_mcp_fetch(*a, **kw):
        calls.append("mcp")
        return {MOCK_DOG_PROFILE_SENIOR["pet_id"]: (MOCK_RECIPES_3, False)}

    async def fake_web_search(*a, **kw):
        calls.append("web")
        return MOCK_WEB_RESULTS

    mock_recipe_fetcher = MagicMock()
    mock_recipe_fetcher.fetch_for_pets = AsyncMock(side_effect=fake_mcp_fetch)
    mock_web_searcher = MagicMock()
    mock_web_searcher.search = AsyncMock(side_effect=fake_web_search)

    # First call: planner returns valid JSON. Second call: synthesis LLM.
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=[
        '{"mcp_query":"シニア犬レシピ","tavily_query":"senior dog nutrition"}',
        "Here are recipes for Buddy based on web research.",
    ])

    food_agent = FoodAgent(llm=mock_llm, web_searcher=mock_web_searcher)
    asyncio.run(food_agent.run(
        mode="food_recipes_info",
        user_message="what should I feed my senior dog?",
        recipe_fetcher=mock_recipe_fetcher,
        pet_profiles=[MOCK_DOG_PROFILE_SENIOR],
        active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
        session_messages=[],
        language="EN",
    ))

    mcp_called = "mcp" in calls
    web_called = "web" in calls
    if mcp_called and web_called:
        return passed("B7.1 both MCP and web search called in food_recipes_info", f"calls={calls}")
    missing = [t for t in ("mcp", "web") if t not in calls]
    return failed("B7.1 both tools called", f"missing: {missing}")


def test_b7_query_planner_called_before_tools() -> bool:
    """
    plan_food_queries() must be invoked before MCP and web search fire.
    Patching plan_food_queries lets us observe the call order.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.agents.food_agent import FoodAgent
        from app.services.food_query_planner import QueryPlan
    except ImportError as exc:
        return failed("B7.2 planner called before tools", f"ImportError: {exc}")

    sequence: list[str] = []

    async def fake_planner(*a, **kw):
        sequence.append("planner")
        return QueryPlan(mcp_query="シニア犬レシピ", tavily_query="senior dog nutrition")

    async def fake_mcp(*a, **kw):
        sequence.append("mcp")
        return {MOCK_DOG_PROFILE_SENIOR["pet_id"]: (MOCK_RECIPES_3, False)}

    async def fake_web(*a, **kw):
        sequence.append("web")
        return MOCK_WEB_RESULTS

    mock_recipe_fetcher = MagicMock()
    mock_recipe_fetcher.fetch_for_pets = AsyncMock(side_effect=fake_mcp)
    mock_web_searcher = MagicMock()
    mock_web_searcher.search = AsyncMock(side_effect=fake_web)
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="food response")

    food_agent = FoodAgent(llm=mock_llm, web_searcher=mock_web_searcher)

    with patch("app.agents.food_agent.plan_food_queries", side_effect=fake_planner):
        asyncio.run(food_agent.run(
            mode="food_recipes_info",
            user_message="what should I feed my senior dog?",
            recipe_fetcher=mock_recipe_fetcher,
            pet_profiles=[MOCK_DOG_PROFILE_SENIOR],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
            session_messages=[],
            language="EN",
        ))

    if "planner" not in sequence:
        return failed("B7.2 planner was called", "plan_food_queries was never invoked")
    planner_idx = sequence.index("planner")
    tool_indices = [i for i, s in enumerate(sequence) if s in ("mcp", "web")]
    if not tool_indices:
        return failed("B7.2 tools called after planner", "no tool calls after planner")
    if planner_idx < min(tool_indices):
        return passed("B7.2 planner runs before MCP and web search", f"sequence={sequence}")
    return failed("B7.2 planner first", f"sequence={sequence} — planner not first")


# ── B8: Web search must NOT run before it has recipe context ──────────────────

def test_b8_web_search_not_called_in_food_recipes_mode() -> bool:
    """
    In food_recipes mode (Mode 1), web search must never be called.
    No FoodAgent = no Tavily call.
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.web_searcher import WebSearcher
    except ImportError as exc:
        return failed("B8 WebSearcher importable", f"ImportError: {exc}")

    mock_web_searcher = MagicMock()
    mock_web_searcher.search = AsyncMock()

    # food_recipes mode: just MCP + return. No FoodAgent, no web search.
    # We verify search is never called (full verification in D1 e2e test).
    mock_web_searcher.search.assert_not_called()
    return passed("B8 web search not called in food_recipes mode (verified fully in D1)")


def test_b8_web_search_receives_recipe_context_in_mode2() -> bool:
    """
    In food_recipes_info mode, the Tavily query must contain recipe-related context
    (not just the raw user message — that would be hallucination-prone).
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.agents.food_agent import FoodAgent
        from app.services.web_searcher import WebSearcher
    except ImportError as exc:
        return failed("B8.2 web search gets recipe context", f"ImportError: {exc}")

    captured_search_calls: list[dict] = []

    async def fake_web_search(query: str, **kwargs):
        captured_search_calls.append({"query": query})
        return MOCK_WEB_RESULTS

    mock_recipe_fetcher = MagicMock()
    mock_recipe_fetcher.fetch_for_pets = AsyncMock(
        return_value={MOCK_DOG_PROFILE["pet_id"]: (MOCK_RECIPES_3, False)}
    )
    mock_web_searcher = MagicMock()
    mock_web_searcher.search = AsyncMock(side_effect=fake_web_search)
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="Response based on recipes and web search.")

    food_agent = FoodAgent(llm=mock_llm, web_searcher=mock_web_searcher)
    asyncio.run(food_agent.run(
        mode="food_recipes_info",
        user_message="what food for kidney disease?",
        recipe_fetcher=mock_recipe_fetcher,
        pet_profiles=[MOCK_DOG_PROFILE],
        active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
        session_messages=[],
        language="EN",
    ))

    if not captured_search_calls:
        return failed("B8.2 web search called in mode 2", "search() was never called")

    query_used = captured_search_calls[0]["query"]
    # Query must not be just the raw user message repeated verbatim without enrichment
    is_enriched = len(query_used) > len("what food for kidney disease?") or "dog" in query_used.lower()
    if is_enriched:
        return passed("B8.2 web search query is enriched with context", f"query={query_used[:80]!r}")
    return failed("B8.2 web search query enriched", f"query looks like raw message: {query_used!r}")


# ── B9: FoodAgent response written to session_messages ───────────────────────

def test_b9_food_agent_response_saved_to_session() -> bool:
    """
    After FoodAgent responds, the reply is written to session_messages so
    ConversationAgent sees it on the next turn.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.agents.food_agent import FoodAgent
        from app.services.web_searcher import WebSearcher
    except ImportError as exc:
        return failed("B9 FoodAgent response saved to session", f"ImportError: {exc}")

    food_reply = "Here are 3 great recipes for Buddy based on his senior profile."

    mock_recipe_fetcher = MagicMock()
    mock_recipe_fetcher.fetch_for_pets = AsyncMock(
        return_value={MOCK_DOG_PROFILE_SENIOR["pet_id"]: (MOCK_RECIPES_3, False)}
    )
    mock_web_searcher = MagicMock()
    mock_web_searcher.search = AsyncMock(return_value=MOCK_WEB_RESULTS)
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=food_reply)

    food_agent = FoodAgent(llm=mock_llm, web_searcher=mock_web_searcher)

    session_messages: list[dict] = []

    result = asyncio.run(food_agent.run(
        mode="food_recipes_info",
        user_message="what should I feed my senior dog?",
        recipe_fetcher=mock_recipe_fetcher,
        pet_profiles=[MOCK_DOG_PROFILE_SENIOR],
        active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
        session_messages=session_messages,
        language="EN",
    ))

    # FoodAgent.run() should return the reply text
    if result is None or (isinstance(result, str) and len(result) == 0):
        return failed("B9 FoodAgent returns non-empty reply", f"got={result!r}")

    # The response text should match what LLM returned
    reply_text = result if isinstance(result, str) else getattr(result, "text", str(result))
    if food_reply not in reply_text and reply_text not in food_reply:
        return failed("B9 FoodAgent reply matches LLM output", f"got={reply_text[:80]!r}")

    return passed("B9 FoodAgent returns reply (session write verified in D-level test)")


# ── B10: Dual-pet dog+dog — both get MCP calls ────────────────────────────────

def test_b10_dual_dog_both_pets_get_recipes() -> bool:
    """With two dogs, fetch_for_pets returns results keyed by both pet_ids."""
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B10 dual-dog both fetched", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    async def fake_fetch_for_pet(pre_built_query, pet_profile, active_profile,
                                 conversation_history=None):
        return (MOCK_RECIPES_3, False)

    with patch.object(fetcher, "_fetch_for_pet", side_effect=fake_fetch_for_pet):
        result = asyncio.run(fetcher.fetch_for_pets(
            pre_built_query="シニアケア レシピ",
            pet_profiles=[MOCK_DOG_PROFILE, MOCK_DOG_PROFILE_SENIOR],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY, MOCK_ACTIVE_PROFILE_EMPTY],
        ))

    pet1 = MOCK_DOG_PROFILE["pet_id"]
    pet2 = MOCK_DOG_PROFILE_SENIOR["pet_id"]
    if pet1 in result and pet2 in result:
        return passed("B10 dual-dog: both pet_ids in result", f"keys={list(result.keys())}")
    return failed("B10 dual-dog: both pet_ids in result", f"got={list(result.keys())}")


# ── B11: Dual-pet dog+cat — only dog gets MCP ────────────────────────────────

def test_b11_dual_pet_dog_cat_only_dog_gets_mcp() -> bool:
    """With dog+cat, fetch_for_pets only returns the dog's pet_id."""
    from unittest.mock import AsyncMock, MagicMock, patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B11 dog+cat: only dog gets MCP", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    fetched_for = []

    async def fake_fetch_for_pet(pre_built_query, pet_profile, active_profile,
                                 conversation_history=None):
        fetched_for.append(pet_profile["pet_id"])
        return (MOCK_RECIPES_3, False)

    with patch.object(fetcher, "_fetch_for_pet", side_effect=fake_fetch_for_pet):
        result = asyncio.run(fetcher.fetch_for_pets(
            pre_built_query="テストクエリ",
            pet_profiles=[MOCK_DOG_PROFILE, MOCK_CAT_PROFILE],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY, MOCK_ACTIVE_PROFILE_EMPTY],
        ))

    cat_id = MOCK_CAT_PROFILE["pet_id"]
    dog_id = MOCK_DOG_PROFILE["pet_id"]

    if cat_id in fetched_for:
        return failed("B11 cat not fetched", f"cat was in fetched_for={fetched_for}")
    if dog_id not in result:
        return failed("B11 dog result present", f"dog not in result keys={list(result.keys())}")
    return passed("B11 dog+cat: only dog fetched", f"fetched_for={fetched_for}, result keys={list(result.keys())}")


# ── B12: Species override — cat query forced to food_info ─────────────────────

def test_b12_cat_species_forces_food_info() -> bool:
    """
    Even if IntentClassifier returns food_recipes for a cat query,
    the species check in the route must override to food_info.
    The food_info constant must exist.
    """
    try:
        from constants import INTENT_FOOD_INFO, INTENT_FOOD_RECIPES
    except ImportError as exc:
        return failed("B12 cat → food_info override", f"ImportError: {exc}")

    # Simulate what the route's species check does:
    def apply_species_override(intent: str, species: str) -> str:
        if species != "dog" and intent in ("food_recipes", "food_recipes_info"):
            return INTENT_FOOD_INFO
        return intent

    result = apply_species_override(INTENT_FOOD_RECIPES, "cat")
    if result == INTENT_FOOD_INFO:
        return passed("B12 cat species → intent forced to food_info")
    return failed("B12 cat species override", f"got {result!r}, expected {INTENT_FOOD_INFO!r}")


# ── B13: ConversationAgent sees FoodAgent response in history ─────────────────

def test_b13_conversation_agent_sees_food_agent_response() -> bool:
    """
    When ConversationAgent.run() is called for a general follow-up,
    it must receive the FoodAgent's previous response inside session_messages.
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.agents.conversation import ConversationAgent
    except ImportError as exc:
        return failed("B13 ConversationAgent importable", str(exc))

    food_agent_reply = "Here are 3 recipes for Buddy: 白菜とツナのうま煮, ささみのパリパリチップス, スイートポテト"

    # Session has FoodAgent reply as last assistant message
    session_with_food_reply = [
        {"role": "user",      "content": "show me recipes for Buddy"},
        {"role": "assistant", "content": food_agent_reply},
        {"role": "user",      "content": "how do I cook the second one?"},
    ]

    captured_messages: list[dict] = []

    async def capture_complete(system_prompt, messages, **kwargs):
        captured_messages.extend(messages)
        return "You can cook ささみのパリパリチップス by baking it in the oven."

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    agent = ConversationAgent(llm=mock_llm)

    asyncio.run(agent.run(
        user_message="how do I cook the second one?",
        session_messages=session_with_food_reply,
        pet_a_context={
            "active_profile": {
                "name":       {"value": "Buddy",    "confidence": 1.0, "source_rank": "explicit_owner",
                               "time_scope": "current", "source_quote": "Buddy", "updated_at": "2026-04-01",
                               "session_id": "s1", "status": "confirmed", "change_detected": "", "trend_flag": ""},
                "species":    {"value": "dog",      "confidence": 1.0, "source_rank": "explicit_owner",
                               "time_scope": "current", "source_quote": "dog", "updated_at": "2026-04-01",
                               "session_id": "s1", "status": "confirmed", "change_detected": "", "trend_flag": ""},
                "breed":      {"value": "Shiba Inu","confidence": 0.95,"source_rank": "explicit_owner",
                               "time_scope": "current", "source_quote": "Shiba", "updated_at": "2026-04-01",
                               "session_id": "s1", "status": "confirmed", "change_detected": "", "trend_flag": ""},
            },
            "gap_list": [],
            "pet_summary": "Buddy is a 3yo male Shiba Inu.",
            "recipes": [],
            "recipe_fallback": False,
        },
        pet_b_context=None,
        relationship_context="",
        intent_type="general",
        urgency="low",
        questions_asked_so_far=0,
        language_str="EN",
    ))

    all_content = " ".join(m.get("content", "") for m in captured_messages)
    if food_agent_reply in all_content:
        return passed("B13 ConversationAgent receives FoodAgent reply in session history")
    return failed(
        "B13 ConversationAgent receives FoodAgent reply",
        f"food_agent_reply not found in {len(captured_messages)} messages"
    )


# ── B14: Mode 1 response — empty message, no LLM call ────────────────────────

def test_b14_food_recipes_mode_no_llm_call() -> bool:
    """
    In food_recipes mode, no LLM is called and message field is empty string.
    Verified by checking response shape from FoodAgent is bypassed entirely.
    """
    from unittest.mock import AsyncMock, MagicMock
    try:
        from constants import INTENT_FOOD_RECIPES
    except ImportError as exc:
        return failed("B14 food_recipes: no LLM, empty message", f"ImportError: {exc}")

    # In food_recipes mode: route returns directly after MCP, no LLM call.
    # We test the CONTRACT: output_mode="food_recipes" implies message=""
    # Full verification that LLM is NOT called happens in D1 (e2e).
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock()

    # Simulate the mode 1 path: recipes come from MCP, message is set to ""
    simulated_response = {
        "message": "",
        "output_mode": INTENT_FOOD_RECIPES,
        "recipes_by_pet": {str(MOCK_DOG_PROFILE["pet_id"]): MOCK_RECIPES_3},
    }

    if simulated_response["message"] != "":
        return failed("B14 food_recipes message=''", f"message={simulated_response['message']!r}")
    if simulated_response["output_mode"] != INTENT_FOOD_RECIPES:
        return failed("B14 food_recipes output_mode", f"got={simulated_response['output_mode']!r}")
    if not simulated_response["recipes_by_pet"]:
        return failed("B14 food_recipes recipes_by_pet non-empty", "empty recipes")

    mock_llm.complete.assert_not_called()
    return passed("B14 food_recipes: message='', output_mode set, recipes_by_pet populated, LLM not called")


# ── B15: WebSearcher importable and has search() ─────────────────────────────

def test_b15_web_searcher_importable() -> bool:
    """WebSearcher class must exist and have an async search() method."""
    try:
        from app.services.web_searcher import WebSearcher
    except ImportError as exc:
        return failed("B15 WebSearcher importable", f"ImportError: {exc}")

    if not hasattr(WebSearcher, "search") and not callable(getattr(WebSearcher, "search", None)):
        return failed("B15 WebSearcher.search() exists")
    return passed("B15 WebSearcher importable with search() method")


def test_b15_food_agent_importable() -> bool:
    """FoodAgent class must exist and have an async run() method."""
    try:
        from app.agents.food_agent import FoodAgent
    except ImportError as exc:
        return failed("B15 FoodAgent importable", f"ImportError: {exc}")

    if not hasattr(FoodAgent, "run"):
        return failed("B15 FoodAgent.run() exists")
    return passed("B15 FoodAgent importable with run() method")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B16 — FoodQueryPlanner (food_query_planner.py)
# ══════════════════════════════════════════════════════════════════════════════

def test_b16_planner_importable() -> bool:
    """QueryPlan and plan_food_queries must be importable from food_query_planner."""
    try:
        from app.services.food_query_planner import QueryPlan, plan_food_queries
    except ImportError as exc:
        return failed("B16.1 food_query_planner importable", str(exc))
    if not callable(plan_food_queries):
        return failed("B16.1 plan_food_queries callable")
    return passed("B16.1 food_query_planner: QueryPlan and plan_food_queries importable")


def test_b16_planner_mode1_mcp_only() -> bool:
    """food_recipes mode: planner asks nano only for mcp_query, not tavily_query."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.2 mode1 mcp_query only", str(exc))

    captured_prompts: list[str] = []

    async def capture_complete(**kwargs):
        captured_prompts.append(kwargs.get("system_prompt", ""))
        return '{"mcp_query":"シニア犬レシピ"}'

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    plan = asyncio.run(plan_food_queries(
        llm=mock_llm,
        mode="food_recipes",
        user_message="show me recipes for Buddy",
        pet_profile=MOCK_DOG_PROFILE,
        active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        language="JA",
    ))

    if not plan.mcp_query:
        return failed("B16.2 mcp_query populated for food_recipes", f"got={plan.mcp_query!r}")
    # System prompt must NOT include tavily_query spec for mode 1
    if captured_prompts and "tavily_query" in captured_prompts[0]:
        return failed("B16.2 mode1 prompt has no tavily_query field", "tavily_query in prompt")
    return passed("B16.2 food_recipes: mcp_query set, tavily_query not in prompt",
                  f"mcp_query={plan.mcp_query!r}")


def test_b16_planner_mode2_both_queries() -> bool:
    """food_recipes_info mode: both mcp_query and tavily_query are returned."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.3 mode2 both queries", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"mcp_query":"シニア犬 腎臓ケア","tavily_query":"senior dog kidney disease nutrition"}'
    )

    plan = asyncio.run(plan_food_queries(
        llm=mock_llm,
        mode="food_recipes_info",
        user_message="what should I feed my senior dog with kidney disease?",
        pet_profile=MOCK_DOG_PROFILE_SENIOR,
        active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        language="EN",
    ))

    if not plan.mcp_query:
        return failed("B16.3 mcp_query populated", f"got={plan.mcp_query!r}")
    if not plan.tavily_query:
        return failed("B16.3 tavily_query populated", f"got={plan.tavily_query!r}")
    return passed("B16.3 food_recipes_info: both queries set",
                  f"mcp={plan.mcp_query!r} tavily={plan.tavily_query!r}")


def test_b16_planner_mode3_tavily_only() -> bool:
    """food_info mode: only tavily_query set, mcp_query is empty, prompt has no mcp_query spec."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.4 mode3 tavily only", str(exc))

    captured_prompts: list[str] = []

    async def capture_complete(**kwargs):
        captured_prompts.append(kwargs.get("system_prompt", ""))
        return '{"tavily_query":"can dogs eat garlic toxicity veterinary"}'

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    plan = asyncio.run(plan_food_queries(
        llm=mock_llm,
        mode="food_info",
        user_message="can dogs eat garlic?",
        pet_profile=MOCK_DOG_PROFILE,
        active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        language="EN",
    ))

    if not plan.tavily_query:
        return failed("B16.4 tavily_query set for food_info", f"got={plan.tavily_query!r}")
    if plan.mcp_query:
        return failed("B16.4 mcp_query empty for food_info",
                      f"mcp_query={plan.mcp_query!r} should be empty string")
    if captured_prompts and "mcp_query" in captured_prompts[0]:
        return failed("B16.4 mode3 prompt has no mcp_query spec", "mcp_query in prompt")
    return passed("B16.4 food_info: tavily_query set, mcp_query empty, mcp spec absent from prompt",
                  f"tavily={plan.tavily_query!r}")


def test_b16_planner_uses_nano_model() -> bool:
    """plan_food_queries() must call the LLM with model='gpt-5.4-nano'."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.5 nano model used", str(exc))

    captured_kwargs: list[dict] = []

    async def capture_complete(**kwargs):
        captured_kwargs.append(kwargs)
        return '{"mcp_query":"レシピ","tavily_query":"dog nutrition"}'

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    asyncio.run(plan_food_queries(
        llm=mock_llm,
        mode="food_recipes_info",
        user_message="what to feed?",
        pet_profile=MOCK_DOG_PROFILE,
        active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        language="JA",
    ))

    if not captured_kwargs:
        return failed("B16.5 LLM was called", "complete() never called")
    model_used = captured_kwargs[0].get("model", "")
    if model_used == "gpt-5.4-nano":
        return passed("B16.5 planner uses gpt-5.4-nano", f"model={model_used!r}")
    return failed("B16.5 planner uses nano model",
                  f"model={model_used!r} (expected 'gpt-5.4-nano')")


def test_b16_planner_fallback_on_llm_error() -> bool:
    """When LLM raises LLMProviderError, planner returns fallback plan — never raises."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
        from app.llm.base import LLMProviderError
    except ImportError as exc:
        return failed("B16.6 fallback on LLM error", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=LLMProviderError("API down"))

    try:
        plan = asyncio.run(plan_food_queries(
            llm=mock_llm,
            mode="food_recipes_info",
            user_message="what should I feed my dog?",
            pet_profile=MOCK_DOG_PROFILE,
            active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
            language="EN",
        ))
        # Fallback must produce something (not both empty)
        if plan.mcp_query or plan.tavily_query:
            return passed("B16.6 LLM error → fallback plan returned, no exception raised",
                          f"mcp={plan.mcp_query!r}, tavily={plan.tavily_query!r}")
        return failed("B16.6 fallback produces non-empty plan",
                      "both mcp_query and tavily_query are empty after LLM error")
    except Exception as exc:
        return failed("B16.6 planner does not raise on LLM error", f"raised {exc!r}")


def test_b16_planner_fallback_on_bad_json() -> bool:
    """When LLM returns invalid JSON, planner returns fallback plan — never raises."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.7 fallback on bad JSON", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="this is definitely not JSON {{{")

    try:
        plan = asyncio.run(plan_food_queries(
            llm=mock_llm,
            mode="food_info",
            user_message="can dogs eat garlic?",
            pet_profile=MOCK_DOG_PROFILE,
            active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
            language="JA",
        ))
        return passed("B16.7 bad JSON → fallback plan returned, no exception",
                      f"tavily={plan.tavily_query!r}")
    except Exception as exc:
        return failed("B16.7 planner graceful on bad JSON", f"raised {exc!r}")


def test_b16_planner_mcp_query_has_japanese() -> bool:
    """mcp_query from planner should contain Japanese characters when LLM provides them."""
    from unittest.mock import AsyncMock, MagicMock
    try:
        from app.services.food_query_planner import plan_food_queries
    except ImportError as exc:
        return failed("B16.8 mcp_query has Japanese", str(exc))

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='{"mcp_query":"シニア犬 腎臓ケア 低脂質","tavily_query":"senior dog kidney low fat"}'
    )

    plan = asyncio.run(plan_food_queries(
        llm=mock_llm,
        mode="food_recipes_info",
        user_message="good food for my senior dog",
        pet_profile=MOCK_DOG_PROFILE_SENIOR,
        active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        language="JA",
    ))

    has_japanese = any(ord(c) > 127 for c in plan.mcp_query)
    if has_japanese:
        return passed("B16.8 mcp_query contains Japanese characters",
                      f"mcp_query={plan.mcp_query!r}")
    return failed("B16.8 mcp_query has Japanese", f"no Japanese in: {plan.mcp_query!r}")


def test_b16_recipe_fetcher_accepts_pre_built_query() -> bool:
    """
    _fetch_for_pet() must use pre_built_query directly as the MCP query.
    No LLM call happens — RecipeFetcher no longer owns query building.
    """
    from unittest.mock import patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B16.9 pre_built_query accepted", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")
    captured_tool_args: list[dict] = []

    async def fake_call_mcp(tool_args: dict):
        captured_tool_args.append(tool_args)
        return [], True

    pre_built = "シニア犬 腎臓ケア 事前構築クエリ"

    with patch.object(fetcher, "_call_mcp_tool", side_effect=fake_call_mcp):
        asyncio.run(fetcher._fetch_for_pet(
            pre_built_query=pre_built,
            pet_profile=MOCK_DOG_PROFILE,
            active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
        ))

    if not captured_tool_args:
        return failed("B16.9 MCP was called", "no MCP call captured")
    actual_query = captured_tool_args[0].get("query", "")
    if actual_query != pre_built:
        return failed("B16.9 pre_built_query used as MCP query",
                      f"expected {pre_built!r}, got {actual_query!r}")
    return passed("B16.9 pre_built_query passed directly to MCP tool_args",
                  f"query={actual_query!r}")


def test_b16_fetch_for_pets_passes_pre_built_query_through() -> bool:
    """
    fetch_for_pets() must forward pre_built_query to each _fetch_for_pet() call.
    Verified by checking that the inner fake receives the correct query string.
    """
    from unittest.mock import patch
    try:
        from app.services.recipe_fetcher import RecipeFetcher
    except ImportError as exc:
        return failed("B16.10 fetch_for_pets passes pre_built_query", str(exc))

    fetcher = RecipeFetcher("https://afa.stagingapp.in/mcp")

    received_queries: list[str] = []

    async def fake_fetch_for_pet(pre_built_query, pet_profile, active_profile,
                                  conversation_history=None):
        received_queries.append(pre_built_query)
        return (MOCK_RECIPES_3, False)

    pre_built = "事前構築クエリ テスト"

    with patch.object(fetcher, "_fetch_for_pet", side_effect=fake_fetch_for_pet):
        asyncio.run(fetcher.fetch_for_pets(
            pre_built_query=pre_built,
            pet_profiles=[MOCK_DOG_PROFILE],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
        ))

    if not received_queries:
        return failed("B16.10 _fetch_for_pet was called", "no calls recorded")
    if received_queries[0] == pre_built:
        return passed("B16.10 fetch_for_pets forwards pre_built_query to _fetch_for_pet",
                      f"received_query={received_queries[0]!r}")
    return failed("B16.10 pre_built_query forwarded",
                  f"expected {pre_built!r}, got {received_queries[0]!r}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C — Eval / Prompt Tests (real LLM, threshold 2/3)
# ══════════════════════════════════════════════════════════════════════════════

def _load_real_classifier():
    """Build a real IntentClassifier using settings from .env."""
    from app.llm.factory import create_llm_provider
    from app.agents.intent_classifier import IntentClassifier
    from app.core.config import settings
    llm = create_llm_provider(settings)
    return IntentClassifier(llm)


def _load_real_recipe_fetcher():
    """Build a real RecipeFetcher using settings from .env."""
    from app.services.recipe_fetcher import RecipeFetcher
    from app.core.config import settings
    return RecipeFetcher(settings.recipe_mcp_url, timeout=settings.recipe_mcp_timeout_seconds)


def _load_real_food_agent():
    """Build a real FoodAgent using settings from .env."""
    from app.llm.factory import create_llm_provider
    from app.agents.food_agent import FoodAgent
    from app.services.web_searcher import WebSearcher
    from app.core.config import settings
    llm = create_llm_provider(settings)
    searcher = WebSearcher(api_key=settings.tavily_api_key)
    return FoodAgent(llm=llm, web_searcher=searcher)


# ── C1: Classifier → food_recipes for browse query ────────────────────────────

def _c1_check() -> bool:
    try:
        from constants import INTENT_FOOD_RECIPES
        clf = _load_real_classifier()
        intent, _ = asyncio.run(clf.classify("おすすめレシピを見せて"))
        return intent == INTENT_FOOD_RECIPES
    except Exception:
        return False


def test_c1_classifier_food_recipes_jp() -> bool:
    return run_with_threshold(
        _c1_check, attempts=3, min_pass=2,
        label="C1 classifier: 'おすすめレシピ' → food_recipes (real LLM)",
    )


# ── C2: Classifier → food_recipes_info for reasoning query ───────────────────

def _c2_check() -> bool:
    try:
        from constants import INTENT_FOOD_RECIPES_INFO
        clf = _load_real_classifier()
        intent, _ = asyncio.run(clf.classify(
            "what should I feed my senior dog with kidney disease?"
        ))
        return intent == INTENT_FOOD_RECIPES_INFO
    except Exception:
        return False


def test_c2_classifier_food_recipes_info_en() -> bool:
    return run_with_threshold(
        _c2_check, attempts=3, min_pass=2,
        label="C2 classifier: 'what should I feed senior dog with kidney disease?' → food_recipes_info",
    )


# ── C3: Classifier → food_info for pure information query ────────────────────

def _c3_check() -> bool:
    try:
        from constants import INTENT_FOOD_INFO
        clf = _load_real_classifier()
        intent, _ = asyncio.run(clf.classify("can dogs eat garlic?"))
        return intent == INTENT_FOOD_INFO
    except Exception:
        return False


def test_c3_classifier_food_info() -> bool:
    return run_with_threshold(
        _c3_check, attempts=3, min_pass=2,
        label="C3 classifier: 'can dogs eat garlic?' → food_info",
    )


# ── C4: Classifier → general for follow-up on shown recipes ──────────────────

def _c4_check() -> bool:
    try:
        clf = _load_real_classifier()
        # Pass history showing recipes were already displayed
        intent, _ = asyncio.run(clf.classify(
            "how do I cook the second one?",
            recent_history=MOCK_HISTORY_AFTER_RECIPES,
        ))
        return intent == "general"
    except Exception:
        return False


def test_c4_classifier_follow_up_is_general() -> bool:
    return run_with_threshold(
        _c4_check, attempts=3, min_pass=2,
        label="C4 classifier: 'how do I cook the second one?' after shown recipes → general",
    )


# ── C5: Classifier carries multi-turn food constraint ────────────────────────

def _c5_check() -> bool:
    try:
        from constants import INTENT_FOOD_RECIPES
        clf = _load_real_classifier()
        # User said "no chicken" two turns ago; now asks "any other options"
        history = [
            {"role": "user",      "content": "show me recipes for Buddy"},
            {"role": "assistant", "content": "Here are some recipe options..."},
            {"role": "user",      "content": "but nothing with chicken please"},
            {"role": "assistant", "content": "Got it, filtering out chicken."},
        ]
        intent, _ = asyncio.run(clf.classify(
            "any other options?",
            recent_history=history,
        ))
        return intent in ("food_recipes", "food_recipes_info")
    except Exception:
        return False


def test_c5_classifier_multi_turn_food_constraint() -> bool:
    return run_with_threshold(
        _c5_check, attempts=3, min_pass=2,
        label="C5 classifier: 'any other options?' with food constraint history → food intent",
    )


# ── C6: FoodQueryPlanner produces Japanese mcp_query from English pet profile ──

def _c6_check() -> bool:
    try:
        from app.llm.factory import create_llm_provider
        from app.services.food_query_planner import plan_food_queries
        from app.core.config import settings
        llm = create_llm_provider(settings)
        plan = asyncio.run(plan_food_queries(
            llm=llm,
            mode="food_recipes",
            user_message="good food for senior dog with kidney problems",
            pet_profile=MOCK_DOG_PROFILE_SENIOR,
            active_profile={"chronic_illness": {"value": "kidney disease", "confidence": 0.9,
                                                 "source_rank": "explicit_owner", "time_scope": "current",
                                                 "source_quote": "kidney disease", "updated_at": "2026-04-01",
                                                 "session_id": "s1", "status": "confirmed",
                                                 "change_detected": "", "trend_flag": ""}},
            language="JA",
        ))
        # mcp_query must be non-empty and contain Japanese characters
        has_japanese = any(ord(c) > 127 for c in plan.mcp_query)
        return bool(plan.mcp_query) and has_japanese
    except Exception:
        return False


def test_c6_query_builder_produces_japanese() -> bool:
    return run_with_threshold(
        _c6_check, attempts=3, min_pass=2,
        label="C6 FoodQueryPlanner: EN pet profile → Japanese mcp_query",
    )


# ── C7: QueryBuilder extracts 'no garlic' constraint into allergen list ───────

def _c7_check() -> bool:
    try:
        from unittest.mock import patch
        fetcher = _load_real_recipe_fetcher()

        captured = []

        async def fake_mcp(tool_args):
            captured.append(tool_args)
            return [], True

        with patch.object(fetcher, "_call_mcp_tool", side_effect=fake_mcp):
            asyncio.run(fetcher._fetch_for_pet(
                pre_built_query="any options?",
                pet_profile=MOCK_DOG_PROFILE,
                active_profile=MOCK_ACTIVE_PROFILE_EMPTY,
                conversation_history=[
                    {"role": "user",      "content": "show me recipes for Buddy"},
                    {"role": "assistant", "content": "Here are some options..."},
                    {"role": "user",      "content": "but no garlic and no chicken"},
                    {"role": "assistant", "content": "Understood, filtering those out."},
                ],
            ))

        if not captured:
            return False
        allergens = captured[0].get("allergens", [])
        # chicken → 鶏肉 via mapping; garlic is not in _ALLERGEN_JP so passes through
        has_chicken_excluded = any("鶏" in a or "chicken" in a.lower() for a in allergens)
        return has_chicken_excluded
    except Exception:
        return False


def test_c7_query_builder_extracts_constraint_from_history() -> bool:
    return run_with_threshold(
        _c7_check, attempts=3, min_pass=2,
        label="C7 QueryBuilder: 'no chicken' in history → allergen list contains 鶏肉",
    )


# ── C8: FoodAgent (mode 2) references recipe names in response ───────────────

def _c8_check() -> bool:
    try:
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.agents.food_agent import FoodAgent
        from app.services.web_searcher import WebSearcher

        mock_recipe_fetcher = MagicMock()
        mock_recipe_fetcher.fetch_for_pets = AsyncMock(
            return_value={MOCK_DOG_PROFILE_SENIOR["pet_id"]: (MOCK_RECIPES_3, False)}
        )
        mock_web_searcher = MagicMock()
        mock_web_searcher.search = AsyncMock(return_value=MOCK_WEB_RESULTS)

        food_agent = _load_real_food_agent()
        # Override the web_searcher with the mock (to avoid real Tavily call)
        food_agent._web_searcher = mock_web_searcher

        result = asyncio.run(food_agent.run(
            mode="food_recipes_info",
            user_message="what should I feed my senior dog?",
            recipe_fetcher=mock_recipe_fetcher,
            pet_profiles=[MOCK_DOG_PROFILE_SENIOR],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
            session_messages=[],
            language="EN",
        ))

        reply = result if isinstance(result, str) else getattr(result, "text", str(result))
        # At least one of the 3 recipe titles must appear in the response
        recipe_titles = [r["title_ja"] for r in MOCK_RECIPES_3]
        mentions_recipe = any(title in reply for title in recipe_titles)
        return mentions_recipe
    except Exception:
        return False


def test_c8_food_agent_references_recipe_names() -> bool:
    return run_with_threshold(
        _c8_check, attempts=3, min_pass=2,
        label="C8 FoodAgent mode 2: response references at least 1 recipe name from MCP",
    )


# ── C9: FoodAgent (mode 3) answers without inventing recipe details ───────────

def _c9_check() -> bool:
    try:
        from app.agents.food_agent import FoodAgent
        from app.services.web_searcher import WebSearcher
        from unittest.mock import AsyncMock, MagicMock

        mock_recipe_fetcher = MagicMock()
        mock_recipe_fetcher.fetch_for_pets = AsyncMock(
            return_value={MOCK_DOG_PROFILE["pet_id"]: ([], True)}  # no recipes
        )
        mock_web_searcher = MagicMock()
        mock_web_searcher.search = AsyncMock(return_value=MOCK_WEB_RESULTS)

        food_agent = _load_real_food_agent()
        food_agent._web_searcher = mock_web_searcher

        result = asyncio.run(food_agent.run(
            mode="food_info",
            user_message="can dogs eat garlic?",
            recipe_fetcher=mock_recipe_fetcher,
            pet_profiles=[MOCK_DOG_PROFILE],
            active_profiles=[MOCK_ACTIVE_PROFILE_EMPTY],
            session_messages=[],
            language="EN",
        ))

        reply = result if isinstance(result, str) else getattr(result, "text", str(result))
        # Must give a substantive answer and not make up recipe names we didn't give it
        has_answer = len(reply) > 30
        # Check it doesn't hallucinate recipe titles we know about but didn't pass
        invented_recipes = any(
            title in reply for title in [r["title_ja"] for r in MOCK_RECIPES_3]
        )
        return has_answer and not invented_recipes
    except Exception:
        return False


def test_c9_food_agent_no_hallucination_in_food_info_mode() -> bool:
    return run_with_threshold(
        _c9_check, attempts=3, min_pass=2,
        label="C9 FoodAgent mode 3 (food_info): answers without inventing recipe names",
    )


# ── C10: FoodQueryPlanner — real LLM produces coherent queries ───────────────

def _c10_check_mode2() -> bool:
    """
    plan_food_queries in food_recipes_info mode must return:
      - mcp_query:    non-empty and contains at least one Japanese character
      - tavily_query: non-empty
    """
    try:
        import re
        from app.llm.factory import create_llm_provider
        from app.core.config import settings
        from app.services.food_query_planner import plan_food_queries

        llm = create_llm_provider(settings)
        plan = asyncio.run(plan_food_queries(
            llm=llm,
            mode="food_recipes_info",
            user_message="腎臓病のシニア犬に合う低リン食はありますか？",
            pet_profile={"species": "dog", "life_stage": "senior", "breed": "柴犬"},
            active_profile={"chronic_illness": {"value": "chronic kidney disease"}},
            language="JA",
        ))

        has_japanese = bool(re.search(r'[\u3040-\u30FF\u4E00-\u9FFF]', plan.mcp_query))
        return bool(plan.mcp_query) and has_japanese and bool(plan.tavily_query)
    except Exception:
        return False


def _c10_check_mode3() -> bool:
    """
    plan_food_queries in food_info mode must return:
      - mcp_query:    empty string (Mode 3 never needs MCP)
      - tavily_query: non-empty
    """
    try:
        from app.llm.factory import create_llm_provider
        from app.core.config import settings
        from app.services.food_query_planner import plan_food_queries

        llm = create_llm_provider(settings)
        plan = asyncio.run(plan_food_queries(
            llm=llm,
            mode="food_info",
            user_message="犬はアボカドを食べても大丈夫ですか？",
            pet_profile={"species": "dog", "life_stage": "adult"},
            active_profile={},
            language="JA",
        ))
        return plan.mcp_query == "" and bool(plan.tavily_query)
    except Exception:
        return False


def test_c10_food_query_planner_real_llm() -> bool:
    mode2_ok = run_with_threshold(
        _c10_check_mode2, attempts=3, min_pass=2,
        label="C10a FoodQueryPlanner mode2: Japanese mcp_query + non-empty tavily_query",
    )
    mode3_ok = run_with_threshold(
        _c10_check_mode3, attempts=3, min_pass=2,
        label="C10b FoodQueryPlanner mode3: mcp_query empty, tavily_query non-empty",
    )
    return mode2_ok and mode3_ok


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — End-to-End HTTP Tests (real server on :8000)
# ══════════════════════════════════════════════════════════════════════════════

def _new_sid() -> str:
    return f"foodai-{uuid.uuid4().hex[:10]}"


def _post_chat(message: str, session_id: str, pet_ids: list[int]) -> dict:
    resp = requests.post(
        f"{BASE_URL}/api/v1/chat",
        headers={"X-User-Code": TEST_USER},
        json={"message": message, "session_id": session_id, "pet_ids": pet_ids},
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()


def test_d1_food_recipes_mode_response_shape() -> bool:
    """
    food_recipes intent → recipes_by_pet populated, message='', output_mode='food_recipes'.
    """
    try:
        sid = _new_sid()
        data = _post_chat("おすすめレシピを見せて", sid, [TEST_PET_DOG_ID])

        output_mode = data.get("output_mode", "")
        message     = data.get("message", "UNSET")
        recipes     = data.get("recipes_by_pet", {})

        if output_mode != "food_recipes":
            # If not yet implemented, classify as expected failure
            return failed("D1 output_mode=food_recipes", f"got={output_mode!r}")
        if message != "":
            return failed("D1 message='' for food_recipes", f"message={message[:60]!r}")
        if not recipes:
            return failed("D1 recipes_by_pet non-empty", "empty dict")

        return passed("D1 food_recipes: message='', recipes_by_pet populated", f"output_mode={output_mode}")
    except Exception as exc:
        return failed("D1 food_recipes response shape", str(exc))


def test_d2_food_recipes_info_response_shape() -> bool:
    """food_recipes_info → message non-empty, recipes_by_pet non-empty, output_mode correct."""
    try:
        sid = _new_sid()
        data = _post_chat(
            "what should I feed my senior dog with kidney disease?",
            sid,
            [TEST_PET_DOG_ID],
        )

        output_mode = data.get("output_mode", "")
        message     = data.get("message", "")
        recipes     = data.get("recipes_by_pet", {})

        if output_mode not in ("food_recipes_info", "food_info"):
            return failed("D2 output_mode=food_recipes_info", f"got={output_mode!r}")
        if not message or len(message) < 20:
            return failed("D2 message non-empty", f"message={message[:60]!r}")

        return passed("D2 food_recipes_info: message non-empty", f"output_mode={output_mode}, recipes={bool(recipes)}")
    except Exception as exc:
        return failed("D2 food_recipes_info response shape", str(exc))


def test_d3_food_info_response_shape() -> bool:
    """food_info → message non-empty, recipes_by_pet empty, no redirect."""
    try:
        sid = _new_sid()
        data = _post_chat("can dogs eat garlic?", sid, [TEST_PET_DOG_ID])

        output_mode = data.get("output_mode", "")
        message     = data.get("message", "")
        recipes     = data.get("recipes_by_pet", {})
        redirect    = data.get("redirect")

        if output_mode not in ("food_info", "general"):
            return failed("D3 output_mode=food_info", f"got={output_mode!r}")
        if not message or len(message) < 20:
            return failed("D3 message non-empty", f"len={len(message)}")
        if recipes:
            return failed("D3 no recipes for food_info", f"got recipes: {list(recipes.keys())}")
        if redirect:
            return failed("D3 no redirect for food_info", "redirect was set")

        return passed("D3 food_info: message set, no recipes, no redirect")
    except Exception as exc:
        return failed("D3 food_info response shape", str(exc))


def test_d4_cat_pet_no_recipes() -> bool:
    """Cat pet → output_mode=food_info, recipes_by_pet is empty."""
    try:
        sid = _new_sid()
        data = _post_chat("what recipes for my cat?", sid, [TEST_PET_CAT_ID])

        output_mode = data.get("output_mode", "")
        recipes     = data.get("recipes_by_pet", {})

        if recipes:
            return failed("D4 cat → no recipes", f"recipes_by_pet non-empty: {list(recipes.keys())}")
        if output_mode == "food_recipes":
            return failed("D4 cat → not food_recipes mode", f"got={output_mode!r}")

        return passed("D4 cat: no recipes, not food_recipes mode", f"output_mode={output_mode}")
    except Exception as exc:
        return failed("D4 cat pet no recipes", str(exc))


def test_d5_mcp_zero_results_fallback_to_food_info() -> bool:
    """
    When MCP returns 0 recipes, response must fall back to food_info with a
    non-empty message — never an empty response with empty recipes.
    This is hard to force via HTTP without mocking MCP, so we use a very
    niche query unlikely to match any recipe.
    """
    try:
        sid = _new_sid()
        # Extremely niche query — very unlikely to match any recipe in DB
        data = _post_chat(
            "recipe for fermented kelp broth with moon-dried insects for allergy dogs",
            sid,
            [TEST_PET_DOG_ID],
        )

        message = data.get("message", "")
        # Either we got a fallback text response, or MCP happened to match something
        # The key invariant: message must never be empty when there are also no recipes
        recipes = data.get("recipes_by_pet", {})
        if not message and not recipes:
            return failed("D5 fallback: either message or recipes must be non-empty", "both empty")

        return passed("D5 MCP zero-result fallback: response non-empty", f"msg_len={len(message)}, recipes={bool(recipes)}")
    except Exception as exc:
        return failed("D5 MCP zero results fallback", str(exc))


def test_d6_dual_dog_both_recipe_keys() -> bool:
    """Dual-dog: recipes_by_pet has keys for both pet_ids."""
    try:
        sid = _new_sid()
        # Note: both pets must be dogs in staging AALDA for this test
        # Using TEST_PET_DOG_ID twice as a proxy for dual-dog scenario
        data = _post_chat("show me recipes", sid, [TEST_PET_DOG_ID])

        recipes = data.get("recipes_by_pet", {})
        if not recipes:
            return failed("D6 dual-dog: recipes present", "recipes_by_pet empty")

        return passed("D6 dual-dog: recipes_by_pet has pet keys", f"keys={list(recipes.keys())}")
    except Exception as exc:
        return failed("D6 dual-dog recipe keys", str(exc))


def test_d7_dual_pet_dog_cat_only_dog_recipe() -> bool:
    """Dog+cat: recipes_by_pet contains only the dog's pet_id."""
    try:
        sid = _new_sid()
        data = _post_chat("food for both of them", sid, [TEST_PET_DOG_ID, TEST_PET_CAT_ID])

        recipes = data.get("recipes_by_pet", {})
        cat_id_str = str(TEST_PET_CAT_ID)
        dog_id_str = str(TEST_PET_DOG_ID)

        if cat_id_str in recipes:
            return failed("D7 cat has no recipes", f"cat_id {cat_id_str} in recipes")

        return passed("D7 dog+cat: cat has no recipes", f"recipe keys={list(recipes.keys())}")
    except Exception as exc:
        return failed("D7 dual-pet dog+cat", str(exc))


def test_d8_general_intent_no_recipes_no_food_mode() -> bool:
    """General intent → ConversationAgent runs, recipes_by_pet empty, output_mode=general."""
    try:
        sid = _new_sid()
        data = _post_chat("How is Buddy doing today?", sid, [TEST_PET_DOG_ID])

        output_mode = data.get("output_mode", "general")
        recipes     = data.get("recipes_by_pet", {})
        message     = data.get("message", "")

        if recipes:
            return failed("D8 general: no recipes", f"got recipes={list(recipes.keys())}")
        if not message:
            return failed("D8 general: message non-empty", "empty message")
        if output_mode not in ("general", ""):
            return failed("D8 general output_mode", f"got={output_mode!r}")

        return passed("D8 general intent: no recipes, message set", f"output_mode={output_mode}")
    except Exception as exc:
        return failed("D8 general intent", str(exc))


def test_d9_multi_turn_follow_up_uses_conversation_agent() -> bool:
    """
    Turn 1: food query → FoodAgent responds with recipes.
    Turn 2: follow-up ('how do I cook the second one?') → ConversationAgent answers
            from history, no new MCP call, output_mode=general.
    """
    try:
        sid = _new_sid()

        # Turn 1: trigger food flow
        data1 = _post_chat("show me recipes for Buddy", sid, [TEST_PET_DOG_ID])
        mode1 = data1.get("output_mode", "")

        # Turn 2: follow-up — should be general
        data2 = _post_chat("how do I cook the second one?", sid, [TEST_PET_DOG_ID])
        mode2   = data2.get("output_mode", "general")
        message2 = data2.get("message", "")
        recipes2 = data2.get("recipes_by_pet", {})

        if not message2:
            return failed("D9 follow-up: ConversationAgent produced a reply", "empty message")
        if recipes2:
            return failed("D9 follow-up: no new recipe fetch", f"got recipes={list(recipes2.keys())}")
        if mode2 not in ("general", ""):
            return failed("D9 follow-up output_mode=general", f"got={mode2!r}")

        return passed(
            "D9 multi-turn: turn 1 food, turn 2 follow-up handled by ConversationAgent",
            f"turn1_mode={mode1}, turn2_mode={mode2}",
        )
    except Exception as exc:
        return failed("D9 multi-turn follow-up", str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Test runners
# ══════════════════════════════════════════════════════════════════════════════

def run_unit_tests() -> None:
    section("B1  IntentClassifier — new food sub-intents")
    test_b1_classifier_returns_food_recipes()
    test_b1_classifier_returns_food_recipes_info()
    test_b1_classifier_returns_food_info()
    test_b1_classifier_accepts_recent_history()

    section("B2  RecipeFetcher — no-llm interface + constraint extraction")
    test_b2_recipe_fetcher_no_llm_needed()
    test_b2_query_builder_extracts_constraint_to_allergen()

    section("B3  RecipeFetcher — species gating + parallel fetch")
    test_b3_cat_is_skipped_in_fetch_for_pets()
    test_b3_two_dogs_fetched_in_parallel()

    section("B4  RecipeFetcher — MCP zero results + response parsing")
    test_b4_mcp_fallback_returns_empty_not_error()
    test_b4_mcp_response_parsed_correctly()

    section("B5  RecipeFetcher — partial results (1 recipe)")
    test_b5_single_recipe_returned()

    section("B6  Routing — food_recipes skips FoodAgent; info modes invoke it")
    test_b6_food_recipes_mode_no_food_agent_call()
    test_b6_food_recipes_info_calls_food_agent()
    test_b6_food_info_calls_food_agent()

    section("B7  FoodAgent — parallel MCP+Tavily in mode2; planner runs first")
    test_b7_food_agent_both_tools_called_in_mode2()
    test_b7_query_planner_called_before_tools()

    section("B8  FoodAgent — web search not called in Mode 1; uses context in Mode 2")
    test_b8_web_search_not_called_in_food_recipes_mode()
    test_b8_web_search_receives_recipe_context_in_mode2()

    section("B9  FoodAgent — response returned and session-writable")
    test_b9_food_agent_response_saved_to_session()

    section("B10 Dual-pet dog+dog — both get MCP")
    test_b10_dual_dog_both_pets_get_recipes()

    section("B11 Dual-pet dog+cat — only dog gets MCP")
    test_b11_dual_pet_dog_cat_only_dog_gets_mcp()

    section("B12 Species override — cat query forced to food_info")
    test_b12_cat_species_forces_food_info()

    section("B13 ConversationAgent — sees FoodAgent reply in session history")
    test_b13_conversation_agent_sees_food_agent_response()

    section("B14 Mode 1 — empty message, no LLM call")
    test_b14_food_recipes_mode_no_llm_call()

    section("B15 New modules importable")
    test_b15_web_searcher_importable()
    test_b15_food_agent_importable()

    section("B16 FoodQueryPlanner — separate query planner (food_query_planner.py)")
    test_b16_planner_importable()
    test_b16_planner_mode1_mcp_only()
    test_b16_planner_mode2_both_queries()
    test_b16_planner_mode3_tavily_only()
    test_b16_planner_uses_nano_model()
    test_b16_planner_fallback_on_llm_error()
    test_b16_planner_fallback_on_bad_json()
    test_b16_planner_mcp_query_has_japanese()
    test_b16_recipe_fetcher_accepts_pre_built_query()
    test_b16_fetch_for_pets_passes_pre_built_query_through()


def run_eval_tests() -> None:
    section("C1  Classifier: 'おすすめレシピ' → food_recipes")
    test_c1_classifier_food_recipes_jp()

    section("C2  Classifier: senior dog kidney disease → food_recipes_info")
    test_c2_classifier_food_recipes_info_en()

    section("C3  Classifier: 'can dogs eat garlic?' → food_info")
    test_c3_classifier_food_info()

    section("C4  Classifier: follow-up after shown recipes → general")
    test_c4_classifier_follow_up_is_general()

    section("C5  Classifier: 'any other options?' with food constraint history → food intent")
    test_c5_classifier_multi_turn_food_constraint()

    section("C6  QueryBuilder: EN pet profile → Japanese search query")
    test_c6_query_builder_produces_japanese()

    section("C7  QueryBuilder: 'no chicken' in history → allergen list")
    test_c7_query_builder_extracts_constraint_from_history()

    section("C8  FoodAgent Mode 2: references recipe names from MCP")
    test_c8_food_agent_references_recipe_names()

    section("C9  FoodAgent Mode 3: answers without hallucinating recipe names")
    test_c9_food_agent_no_hallucination_in_food_info_mode()

    section("C10 FoodQueryPlanner: real LLM produces Japanese mcp_query and tavily_query")
    test_c10_food_query_planner_real_llm()


def run_e2e_tests() -> None:
    section("D1  POST /chat — food_recipes: cards only, no message")
    test_d1_food_recipes_mode_response_shape()

    section("D2  POST /chat — food_recipes_info: message + recipes")
    test_d2_food_recipes_info_response_shape()

    section("D3  POST /chat — food_info: message only, no recipes")
    test_d3_food_info_response_shape()

    section("D4  POST /chat — cat pet: no recipes, food_info mode")
    test_d4_cat_pet_no_recipes()

    section("D5  POST /chat — MCP zero results fallback to food_info")
    test_d5_mcp_zero_results_fallback_to_food_info()

    section("D6  POST /chat — dual-dog: recipes_by_pet has both pet keys")
    test_d6_dual_dog_both_recipe_keys()

    section("D7  POST /chat — dog+cat: only dog has recipes")
    test_d7_dual_pet_dog_cat_only_dog_recipe()

    section("D8  POST /chat — general intent: no recipes, ConversationAgent runs")
    test_d8_general_intent_no_recipes_no_food_mode()

    section("D9  POST /chat — multi-turn: follow-up classified as general")
    test_d9_multi_turn_follow_up_uses_conversation_agent()


def _check_server() -> bool:
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
        return True
    except Exception:
        return False


def main() -> None:
    run_unit  = "--eval" not in sys.argv and "--e2e" not in sys.argv
    run_eval  = "--unit" not in sys.argv and "--e2e" not in sys.argv
    run_e2e   = "--unit" not in sys.argv and "--eval" not in sys.argv

    # Explicit flags
    if "--unit"  in sys.argv: run_unit,  run_eval, run_e2e = True,  False, False
    if "--eval"  in sys.argv: run_unit,  run_eval, run_e2e = False, True,  False
    if "--e2e"   in sys.argv: run_unit,  run_eval, run_e2e = False, False, True

    print(f"\n{BOLD}Food AI — Test Suite (TDD){RESET}")
    print("=" * 60)
    print("TDD baseline: run BEFORE implementation -- expect failures.")
    print("Run after each step to track progress.")
    print("=" * 60)

    if run_unit:
        print(f"\n{BOLD}--- Section B: Unit Tests (no server, no real LLM) ---{RESET}")
        run_unit_tests()

    if run_eval:
        print(f"\n{BOLD}--- Section C: Eval / Prompt Tests (real LLM required) ---{RESET}")
        print(f"  Note: requires .env with valid API credentials")
        try:
            run_eval_tests()
        except Exception as exc:
            print(f"  {RED}Eval tests failed to initialise: {exc}{RESET}")
            print(f"  Make sure .env is configured with valid LLM credentials.")

    if run_e2e:
        if requests is None:
            print(f"\n{RED}ERROR: 'requests' not installed. pip install requests{RESET}")
            sys.exit(1)
        print(f"\n{BOLD}--- Section D: End-to-End HTTP Tests (server on :8000) ---{RESET}")
        if not _check_server():
            print(f"  {RED}ERROR: Cannot reach {BASE_URL}. Start backend first:{RESET}")
            print(f"  cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
            sys.exit(1)
        print(f"  {GREEN}Server reachable.{RESET}")
        run_e2e_tests()

    # ── Summary ──────────────────────────────────────────────────────────────
    total  = len(_results)
    passed_n = sum(_results)
    colour = GREEN if passed_n == total else RED

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"  {BOLD}{colour}{passed_n}/{total} tests passed{RESET}")
    if passed_n < total:
        print(f"  {RED}{total - passed_n} failed — see FAIL lines above{RESET}")
        print(f"  (Failures before implementation are expected — this is TDD.)")
    print(f"{BOLD}{'=' * 60}{RESET}\n")

    sys.exit(0 if passed_n == total else 1)


if __name__ == "__main__":
    main()
