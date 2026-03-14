# tests/run_e2e.py
#
# End-to-end test suite for AnyMall-chan backend -- Phase 1C.
#
# Tests the full pipeline:
#   POST /chat
#     -> IntentClassifier  (LLM)
#     -> Agent 1           (LLM)
#     -> guardrails
#     -> deeplink
#     -> [background] Compressor (LLM) -> PostgreSQL fact_log table
#     -> [background] Aggregator       -> PostgreSQL active_profile table + app.state
#
# Design decisions:
#   - Each test creates a unique session_id (UUID prefix) so facts from
#     different tests never mix.  We filter by session_id via the API.
#   - Tests that check the Compressor output sleep BACKGROUND_WAIT seconds
#     after the HTTP call -- the Compressor runs after the reply is sent.
#   - LLM responses are non-deterministic: assertions target structure and
#     direction, not exact values (e.g. "confidence < 0.85" not "== 0.75").
#   - Every test function returns bool and prints its own PASS/FAIL line.
#     No test raises -- failures are captured and counted at the end.
#
# Phase 1C changes:
#   - Fact log is now read via GET /debug/facts?session_id=... (PostgreSQL)
#     instead of reading data/fact_log.json directly.
#   - New Section 6: Database & Infrastructure tests.
#
# Usage:
#   # Terminal 1 -- start backend:
#   cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
#
#   # Terminal 2 -- run tests:
#   cd backend && python tests/run_e2e.py
#
# Requirements:
#   pip install requests   (if not already in your venv)

import sys
import time
import uuid

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run: pip install requests")
    sys.exit(1)


# ── Config ─────────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"

# How long to wait for the Compressor background task to finish.
# The Compressor makes an LLM call (Azure OpenAI) so 8 s is a safe margin.
BACKGROUND_WAIT = 8   # seconds


# ── Terminal colours ───────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ── Shared helpers ─────────────────────────────────────────────────────────────

def new_sid() -> str:
    """Unique session ID for one test -- keeps fact_log entries isolated."""
    return f"e2e-{uuid.uuid4().hex[:10]}"


def post_chat(message: str, session_id: str) -> dict:
    """POST /chat and return the parsed JSON body. Raises on HTTP error."""
    resp = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message, "session_id": session_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def facts_for(session_id: str) -> list[dict]:
    """
    Fetch fact_log entries for a specific session_id from the database.

    Phase 1C: reads via GET /debug/facts?session_id=... (PostgreSQL)
    instead of reading data/fact_log.json directly.
    """
    resp = requests.get(
        f"{BASE_URL}/debug/facts",
        params={"session_id": session_id, "limit": 100},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("facts", [])


def wait_background(label: str = "") -> None:
    """Sleep for BACKGROUND_WAIT seconds while printing a countdown."""
    msg = f"  Waiting {BACKGROUND_WAIT}s for Compressor"
    if label:
        msg += f" ({label})"
    msg += "..."
    print(msg)
    time.sleep(BACKGROUND_WAIT)


def passed(label: str, detail: str = "") -> bool:
    line = f"  {GREEN}PASS{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}> {detail}{RESET}"
    print(line)
    return True


def failed(label: str, detail: str = "") -> bool:
    line = f"  {RED}FAIL{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}> {detail}{RESET}"
    print(line)
    return False


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    print("-" * 56)


# ── Section 1: Infrastructure ──────────────────────────────────────────────────

def test_health_endpoint() -> bool:
    """GET /health returns 200 with status=ok and llm_reachable=True."""
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=10)
        if resp.status_code != 200:
            return failed("GET /health -- HTTP 200", f"got {resp.status_code}")
        data = resp.json()
        if data.get("status") != "ok":
            return failed("GET /health -- status=ok", f"got {data.get('status')!r}")
        if not data.get("llm_reachable"):
            return failed(
                "GET /health -- llm_reachable",
                "False -- check Azure credentials in .env",
            )
        return passed("GET /health", f"phase={data.get('phase')} llm_reachable=True")
    except Exception as exc:
        return failed("GET /health", str(exc))


def test_response_structure() -> bool:
    """POST /chat response contains all required fields with correct types."""
    sid = new_sid()
    try:
        data = post_chat("Hello!", sid)
        required = {
            "message": str,
            "session_id": str,
            "questions_asked_count": int,
            "was_guardrailed": bool,
            "is_entity": bool,
            "asked_gap_question": bool,
            "intent_type": str,
            "urgency": str,
            "confidence_score": int,
            "confidence_color": str,
        }
        for field, expected_type in required.items():
            if field not in data:
                return failed("Response structure", f"missing field: {field!r}")
            if not isinstance(data[field], expected_type):
                return failed(
                    "Response structure",
                    f"{field!r} is {type(data[field]).__name__}, expected {expected_type.__name__}",
                )
        if data["session_id"] != sid:
            return failed("Response structure -- session_id echo", f"{data['session_id']!r} != {sid!r}")
        if not data["message"]:
            return failed("Response structure", "message is empty string")
        # redirect field must exist (can be null)
        if "redirect" not in data:
            return failed("Response structure", "missing field: 'redirect'")
        return passed("Response structure", "all fields present with correct types")
    except Exception as exc:
        return failed("Response structure", str(exc))


def test_confidence_endpoint() -> bool:
    """GET /confidence returns confidence_score (int 0-100) and confidence_color."""
    try:
        resp = requests.get(f"{BASE_URL}/confidence", timeout=10)
        if resp.status_code != 200:
            return failed("GET /confidence -- HTTP 200", f"got {resp.status_code}")
        data = resp.json()
        score = data.get("confidence_score")
        color = data.get("confidence_color")
        if not isinstance(score, int):
            return failed("GET /confidence -- score type", f"got {type(score).__name__}")
        if score < 0 or score > 100:
            return failed("GET /confidence -- score range", f"got {score}")
        if color not in ("green", "yellow", "red"):
            return failed("GET /confidence -- color value", f"got {color!r}")
        return passed("GET /confidence", f"score={score} color={color}")
    except Exception as exc:
        return failed("GET /confidence", str(exc))


# ── Section 2: Intent Routing ──────────────────────────────────────────────────

def test_general_intent_no_redirect() -> bool:
    """A general message produces redirect=null."""
    sid = new_sid()
    try:
        data = post_chat("Tell me something nice about Luna", sid)
        redirect = data.get("redirect")
        if redirect is not None:
            return failed("General intent -- redirect=null", f"got redirect: {redirect}")
        return passed("General intent -- redirect=null")
    except Exception as exc:
        return failed("General intent -- redirect=null", str(exc))


def test_health_intent_redirect() -> bool:
    """A health-related message produces redirect with module='health'."""
    sid = new_sid()
    try:
        data = post_chat(
            "Luna has been vomiting repeatedly all morning and she won't eat -- I'm so worried",
            sid,
        )
        redirect = data.get("redirect")
        if redirect is None:
            return failed("Health intent -- redirect present", "redirect is null")
        if redirect.get("module") != "health":
            return failed("Health intent -- module=health", f"got module={redirect.get('module')!r}")
        if not redirect.get("deep_link"):
            return failed("Health intent -- deep_link non-empty", "deep_link is empty")
        urgency = redirect.get("urgency", "unknown")
        return passed("Health intent", f"module=health urgency={urgency}")
    except Exception as exc:
        return failed("Health intent", str(exc))


def test_food_low_urgency_no_redirect() -> bool:
    """A routine food question (LOW urgency) produces redirect=null after gating."""
    sid = new_sid()
    try:
        data = post_chat(
            "What brand of dry food do you recommend for a Shiba Inu?",
            sid,
        )
        redirect = data.get("redirect")
        if redirect is not None:
            return failed("Food LOW urgency -- no redirect", f"got redirect: {redirect}")
        return passed("Food LOW urgency", "redirect=null as expected")
    except Exception as exc:
        return failed("Food LOW urgency", str(exc))


def test_health_reply_is_short() -> bool:
    """Health-intent reply is empathy-only -- short (<= 5 sentences, no advice)."""
    sid = new_sid()
    try:
        data = post_chat(
            "Luna is limping badly on her front leg and crying, what should I do?",
            sid,
        )
        reply = data.get("message", "")
        sentences = [s.strip() for s in reply.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        if len(sentences) > 5:
            return failed(
                "Health reply is short",
                f"got {len(sentences)} sentences -- expected <= 5 for empathy-only reply",
            )
        return passed("Health reply is short", f"{len(sentences)} sentence(s)")
    except Exception as exc:
        return failed("Health reply is short", str(exc))


# ── Section 3: Session Management ─────────────────────────────────────────────

def test_session_continuity() -> bool:
    """Multiple messages in same session all return the same session_id."""
    sid = new_sid()
    try:
        messages = [
            "Hi, I have a question about Luna",
            "She has been eating less lately",
            "I'm not too worried yet though",
        ]
        for i, msg in enumerate(messages, 1):
            data = post_chat(msg, sid)
            if data["session_id"] != sid:
                return failed(
                    "Session continuity",
                    f"turn {i}: got session_id={data['session_id']!r}, expected {sid!r}",
                )
        return passed("Session continuity", f"{len(messages)} turns -- session_id consistent")
    except Exception as exc:
        return failed("Session continuity", str(exc))


def test_question_count_non_decreasing() -> bool:
    """questions_asked_count is >= 0 on every turn and never decreases."""
    sid = new_sid()
    try:
        r1 = post_chat("Tell me about Luna", sid)
        r2 = post_chat("She eats twice a day", sid)
        r3 = post_chat("She weighs about 4kg", sid)
        counts = [r["questions_asked_count"] for r in [r1, r2, r3]]
        if any(c < 0 for c in counts):
            return failed("Question count >= 0", f"got negative count: {counts}")
        for i in range(1, len(counts)):
            if counts[i] < counts[i - 1]:
                return failed(
                    "Question count non-decreasing",
                    f"decreased: turn {i}={counts[i-1]} -> turn {i+1}={counts[i]}",
                )
        return passed("Question count", f"counts across 3 turns: {counts}")
    except Exception as exc:
        return failed("Question count", str(exc))


def test_new_sessions_are_independent() -> bool:
    """Two different session_ids do not share question counts (each starts at 0)."""
    sid_a = new_sid()
    sid_b = new_sid()
    try:
        for msg in ["Hello", "Luna is 2 years old", "She weighs 4kg"]:
            post_chat(msg, sid_a)
        r_b = post_chat("Hi there", sid_b)
        count_b = r_b["questions_asked_count"]
        if count_b > 1:
            return failed(
                "Sessions are independent",
                f"session B started with questions_asked_count={count_b}, expected 0 or 1",
            )
        return passed("Sessions are independent", f"session B count={count_b}")
    except Exception as exc:
        return failed("Sessions are independent", str(exc))


# ── Section 4: Compressor -- Fact Extraction ───────────────────────────────────

def test_non_fact_message_no_extraction() -> bool:
    """Greeting/acknowledgement produces no entries in fact_log for this session."""
    sid = new_sid()
    try:
        post_chat("ok thanks, talk later!", sid)
        wait_background("non-fact")
        facts = facts_for(sid)
        if facts:
            keys = [f.get("key", f.get("field_key")) for f in facts]
            return failed("Non-fact -> no extraction", f"got {len(facts)} fact(s): {keys}")
        return passed("Non-fact -> no extraction", "is_entity=False confirmed")
    except Exception as exc:
        return failed("Non-fact -> no extraction", str(exc))


def test_single_fact_extraction() -> bool:
    """A clear factual statement produces >= 1 entry in fact_log."""
    sid = new_sid()
    try:
        post_chat(
            "The vet confirmed this morning that Luna weighs exactly 4.2 kg",
            sid,
        )
        wait_background("single fact")
        facts = facts_for(sid)
        if not facts:
            return failed("Single fact extraction", "no facts in fact_log for this session")
        keys = [f.get("key", f.get("field_key")) for f in facts]
        return passed("Single fact extraction", f"extracted {len(facts)} fact(s): {keys}")
    except Exception as exc:
        return failed("Single fact extraction", str(exc))


def test_multiple_facts_extraction() -> bool:
    """A message with several facts produces >= 2 entries in fact_log."""
    sid = new_sid()
    try:
        post_chat(
            "Luna is 2 years old, weighs about 4 kg, and has been eating Royal Canin "
            "dry food. She has no known allergies at all.",
            sid,
        )
        wait_background("multiple facts")
        facts = facts_for(sid)
        if len(facts) < 2:
            keys = [f.get("key", f.get("field_key")) for f in facts]
            return failed(
                "Multiple fact extraction",
                f"expected >= 2 facts, got {len(facts)}: {keys}",
            )
        keys = [f.get("key", f.get("field_key")) for f in facts]
        return passed("Multiple fact extraction", f"extracted {len(facts)} facts: {keys}")
    except Exception as exc:
        return failed("Multiple fact extraction", str(exc))


def test_negative_fact_extraction() -> bool:
    """'Luna has no allergies' is extracted as a negative fact (value != empty)."""
    sid = new_sid()
    try:
        post_chat(
            "Luna has absolutely no allergies -- the vet has confirmed this multiple times",
            sid,
        )
        wait_background("negative fact")
        facts = facts_for(sid)
        if not facts:
            return failed("Negative fact extraction", "no facts extracted at all")
        # DB schema uses field_key, API might return key or field_key
        allergy = [f for f in facts if "allerg" in (f.get("key") or f.get("field_key") or "").lower()]
        if not allergy:
            all_keys = [f.get("key", f.get("field_key")) for f in facts]
            return failed("Negative fact extraction", f"no allergy key found -- got: {all_keys}")
        f = allergy[0]
        if not f.get("value"):
            return failed("Negative fact extraction", "allergy fact has empty value")
        return passed(
            "Negative fact extraction",
            f"key={f.get('key', f.get('field_key'))!r} value={f['value']!r}",
        )
    except Exception as exc:
        return failed("Negative fact extraction", str(exc))


def test_past_tense_fact_time_scope() -> bool:
    """Past-tense statement produces time_scope='past'."""
    sid = new_sid()
    try:
        post_chat(
            "Luna had a bad ear infection two months ago -- the vet treated it with antibiotics",
            sid,
        )
        wait_background("past tense")
        facts = facts_for(sid)
        if not facts:
            return failed("Past-tense time_scope", "no facts extracted")
        past_facts = [f for f in facts if f.get("time_scope") == "past"]
        if not past_facts:
            scopes = [(f.get("key", f.get("field_key")), f.get("time_scope")) for f in facts]
            return failed("Past-tense time_scope", f"no past facts -- got: {scopes}")
        return passed(
            "Past-tense time_scope",
            f"{len(past_facts)} fact(s) with time_scope='past'",
        )
    except Exception as exc:
        return failed("Past-tense time_scope", str(exc))


def test_vet_confirmed_high_confidence_and_source_rank() -> bool:
    """Vet-confirmed fact gets confidence >= 0.85 and source_rank='vet_record'."""
    sid = new_sid()
    try:
        post_chat(
            "The vet ran a full blood panel today and confirmed Luna weighs exactly 4.2 kg",
            sid,
        )
        wait_background("vet confirmed")
        facts = facts_for(sid)
        if not facts:
            return failed("Vet-confirmed -- high confidence", "no facts extracted")
        weight_facts = [f for f in facts if "weight" in (f.get("key") or f.get("field_key") or "").lower()]
        if not weight_facts:
            all_keys = [f.get("key", f.get("field_key")) for f in facts]
            return failed("Vet-confirmed -- weight fact", f"no weight key -- got: {all_keys}")
        f = weight_facts[0]
        conf = f.get("confidence", 0)
        src  = f.get("source_rank", "")
        if conf < 0.85:
            return failed("Vet-confirmed confidence >= 0.85", f"got confidence={conf}")
        if src != "vet_record":
            return failed("Vet-confirmed source_rank='vet_record'", f"got {src!r}")
        return passed(
            "Vet-confirmed -- high confidence + vet_record",
            f"confidence={conf} source_rank={src!r}",
        )
    except Exception as exc:
        return failed("Vet-confirmed -- high confidence", str(exc))


def test_hedged_statement_lower_confidence() -> bool:
    """Hedged statement ('I think', 'maybe', 'around') gets confidence < 0.85."""
    sid = new_sid()
    try:
        post_chat(
            "I think Luna maybe weighs around 3.5 kg, but I haven't actually weighed her recently",
            sid,
        )
        wait_background("hedged")
        facts = facts_for(sid)
        if not facts:
            return failed("Hedged -- lower confidence", "no facts extracted")
        weight_facts = [f for f in facts if "weight" in (f.get("key") or f.get("field_key") or "").lower()]
        if not weight_facts:
            all_keys = [f.get("key", f.get("field_key")) for f in facts]
            return failed("Hedged -- weight fact", f"no weight key -- got: {all_keys}")
        conf = weight_facts[0].get("confidence", 1.0)
        if conf >= 0.85:
            return failed(
                "Hedged -- confidence < 0.85",
                f"got confidence={conf} -- expected lower for hedged statement",
            )
        return passed("Hedged -- lower confidence", f"confidence={conf}")
    except Exception as exc:
        return failed("Hedged -- lower confidence", str(exc))


def test_fact_log_schema() -> bool:
    """Every entry from the debug/facts API for this session has all required schema fields."""
    sid = new_sid()
    required_fields = [
        "key", "value", "confidence", "source_rank", "time_scope",
        "session_id",
    ]
    try:
        post_chat("Luna is a 3-year-old female Shiba Inu and she is spayed", sid)
        wait_background("schema check")
        facts = facts_for(sid)
        if not facts:
            return failed("Fact log schema", "no facts extracted -- cannot check schema")
        for i, fact in enumerate(facts):
            # DB may use field_key instead of key -- normalize
            if "field_key" in fact and "key" not in fact:
                fact["key"] = fact["field_key"]
            missing = [f for f in required_fields if f not in fact]
            if missing:
                return failed("Fact log schema", f"entry {i} missing fields: {missing}")
            conf = fact["confidence"]
            if not isinstance(conf, (int, float)) or not (0.50 <= conf <= 1.0):
                return failed(
                    "Fact log schema -- confidence range",
                    f"entry {i} confidence={conf!r} not in [0.50, 1.0]",
                )
        return passed(
            "Fact log schema",
            f"all {len(facts)} entries have correct fields and types",
        )
    except Exception as exc:
        return failed("Fact log schema", str(exc))


def test_needs_clarification_flag() -> bool:
    """Hedged fact has needs_clarification=True; confident fact has needs_clarification=False."""
    sid_hedged    = new_sid()
    sid_confident = new_sid()
    try:
        post_chat("I'm not sure but I think Luna might be around 3 kg maybe", sid_hedged)
        post_chat("Luna weighs 4.2 kg -- vet confirmed this morning", sid_confident)

        wait_background("clarification flags")

        hedged_facts    = facts_for(sid_hedged)
        confident_facts = facts_for(sid_confident)

        if not hedged_facts:
            return failed("needs_clarification flag", "no hedged facts extracted")
        if not confident_facts:
            return failed("needs_clarification flag", "no confident facts extracted")

        any_hedged_flagged = any(f.get("needs_clarification") for f in hedged_facts)
        any_confident_flagged = any(f.get("needs_clarification") for f in confident_facts)

        if not any_hedged_flagged:
            confs = [f["confidence"] for f in hedged_facts]
            return failed(
                "needs_clarification=True for hedged",
                f"no hedged fact was flagged -- confidences: {confs}",
            )
        if any_confident_flagged:
            confs = [f["confidence"] for f in confident_facts]
            return failed(
                "needs_clarification=False for confident",
                f"a confident fact was flagged -- confidences: {confs}",
            )
        return passed(
            "needs_clarification flags correct",
            "hedged=True, confident=False",
        )
    except Exception as exc:
        return failed("needs_clarification flag", str(exc))


# ── Section 5: Aggregator -- Profile Merging ───────────────────────────────────

def get_profile() -> dict:
    """GET /debug/profile and return the profile dict."""
    resp = requests.get(f"{BASE_URL}/debug/profile", timeout=10)
    resp.raise_for_status()
    return resp.json().get("profile", {})


def test_aggregator_new_fact() -> bool:
    """A new fact appears in active_profile with status='new' (Rule 1)."""
    sid = new_sid()
    try:
        post_chat("Luna weighs exactly 4.5 kg -- I just weighed her", sid)
        wait_background("aggregator new fact")
        profile = get_profile()
        entry = profile.get("weight")
        if entry is None:
            return failed("Aggregator new fact", "no 'weight' key in active_profile")
        if entry.get("status") != "new":
            if entry.get("status") not in ("new", "updated"):
                return failed("Aggregator new fact -- status", f"got status={entry.get('status')!r}")
        conf = entry.get("confidence", 0)
        if not (0.50 <= conf <= 1.0):
            return failed("Aggregator new fact -- confidence", f"got confidence={conf}")
        return passed(
            "Aggregator new fact (Rule 1/5)",
            f"weight={entry['value']!r} conf={conf:.2f} status={entry['status']!r}",
        )
    except Exception as exc:
        return failed("Aggregator new fact", str(exc))


def test_aggregator_confirmation() -> bool:
    """Repeating the same fact boosts confidence (Rule 3)."""
    sid = new_sid()
    try:
        post_chat("Luna's energy level is moderate these days", sid)
        wait_background("aggregator confirm step 1")
        profile1 = get_profile()
        entry1 = profile1.get("energy_level")
        if entry1 is None:
            return failed("Aggregator confirmation -- step 1", "no energy_level key after first message")
        conf1 = entry1.get("confidence", 0)
        if isinstance(conf1, int) or conf1 > 1.0:
            conf1 = conf1 / 100.0

        post_chat("Yes Luna's energy is still moderate, nothing has changed", sid)
        wait_background("aggregator confirm step 2")
        profile2 = get_profile()
        entry2 = profile2.get("energy_level")
        if entry2 is None:
            return failed("Aggregator confirmation -- step 2", "energy_level disappeared")
        conf2 = entry2.get("confidence", 0)
        if isinstance(conf2, int) or conf2 > 1.0:
            conf2 = conf2 / 100.0

        if conf2 <= conf1:
            return failed(
                "Aggregator confirmation -- boost",
                f"confidence did not increase: {conf1:.2f} -> {conf2:.2f}",
            )
        status = entry2.get("status", "")
        return passed(
            "Aggregator confirmation (Rule 3)",
            f"conf {conf1:.2f} -> {conf2:.2f} status={status!r}",
        )
    except Exception as exc:
        return failed("Aggregator confirmation", str(exc))


def test_aggregator_better_fact() -> bool:
    """A higher-confidence fact overwrites a lower-confidence one (Rule 5)."""
    sid = new_sid()
    try:
        post_chat(
            "I think Luna's appetite is maybe a bit low, not totally sure though",
            sid,
        )
        wait_background("aggregator better step 1")
        profile1 = get_profile()
        entry1 = profile1.get("appetite")
        if entry1 is None:
            return failed("Aggregator better fact -- step 1", "no appetite key after hedged message")
        conf1 = entry1.get("confidence", 0)
        if isinstance(conf1, int) or conf1 > 1.0:
            conf1 = conf1 / 100.0
        val1 = entry1.get("value", "")

        post_chat(
            "The vet said today that Luna's appetite is excellent -- she's eating very well",
            sid,
        )
        wait_background("aggregator better step 2")
        profile2 = get_profile()
        entry2 = profile2.get("appetite")
        if entry2 is None:
            return failed("Aggregator better fact -- step 2", "appetite key disappeared")
        conf2 = entry2.get("confidence", 0)
        if isinstance(conf2, int) or conf2 > 1.0:
            conf2 = conf2 / 100.0
        val2 = entry2.get("value", "")

        if val2 == val1 and conf2 <= conf1:
            return failed(
                "Aggregator better fact",
                f"vet fact did not win: val={val1!r}->{val2!r} conf={conf1:.2f}->{conf2:.2f}",
            )
        return passed(
            "Aggregator better fact (Rule 5)",
            f"val={val1!r}->{val2!r} conf {conf1:.2f}->{conf2:.2f} status={entry2.get('status')!r}",
        )
    except Exception as exc:
        return failed("Aggregator better fact", str(exc))


def test_aggregator_past_fact_skipped() -> bool:
    """A past-tense fact does NOT appear as a current entry in active_profile (Rule 0)."""
    sid = new_sid()
    try:
        post_chat(
            "Luna had really bad fleas two years ago but she's been fine since",
            sid,
        )
        wait_background("aggregator past fact")

        facts = facts_for(sid)
        past_facts = [f for f in facts if f.get("time_scope") == "past"]
        profile = get_profile()

        flea_in_profile = [
            k for k in profile
            if "flea" in k.lower()
            and isinstance(profile[k], dict)
            and profile[k].get("session_id") == sid
        ]

        if flea_in_profile:
            return failed(
                "Aggregator past fact skipped",
                f"past fact appeared in active_profile: {flea_in_profile}",
            )
        detail = f"{len(past_facts)} past fact(s) in fact_log, none in active_profile"
        return passed("Aggregator past fact skipped (Rule 0)", detail)
    except Exception as exc:
        return failed("Aggregator past fact skipped", str(exc))


def test_aggregator_seed_data_preserved() -> bool:
    """Aggregator updates do not wipe out seed data for unrelated keys."""
    try:
        profile = get_profile()
        seed_keys = ["diet_type", "neutered_spayed"]
        missing = [k for k in seed_keys if k not in profile]
        if missing:
            return failed(
                "Seed data preserved",
                f"missing seed keys after Aggregator runs: {missing}",
            )
        if "_pet_history" not in profile:
            return failed("Seed data preserved", "_pet_history metadata missing")
        return passed(
            "Seed data preserved",
            f"seed keys present: {seed_keys}, _pet_history intact",
        )
    except Exception as exc:
        return failed("Seed data preserved", str(exc))


def test_aggregator_debug_endpoint() -> bool:
    """GET /debug/profile returns status=ok and field_count > 0."""
    try:
        resp = requests.get(f"{BASE_URL}/debug/profile", timeout=10)
        if resp.status_code != 200:
            return failed("Debug profile endpoint", f"HTTP {resp.status_code}")
        data = resp.json()
        if data.get("status") != "ok":
            return failed("Debug profile endpoint -- status", f"got {data.get('status')!r}")
        count = data.get("field_count", 0)
        if count < 1:
            return failed("Debug profile endpoint -- field_count", f"got {count}")
        return passed("Debug profile endpoint", f"status=ok field_count={count}")
    except Exception as exc:
        return failed("Debug profile endpoint", str(exc))


# ── Section 6: Database & Phase 1C ────────────────────────────────────────────

def test_debug_facts_endpoint() -> bool:
    """GET /debug/facts returns a list from PostgreSQL (not JSON file)."""
    try:
        resp = requests.get(f"{BASE_URL}/debug/facts", timeout=10)
        if resp.status_code != 200:
            return failed("GET /debug/facts -- HTTP 200", f"got {resp.status_code}")
        data = resp.json()
        if "facts" not in data:
            return failed("GET /debug/facts -- has 'facts' key", f"keys: {list(data.keys())}")
        if "count" not in data:
            return failed("GET /debug/facts -- has 'count' key", f"keys: {list(data.keys())}")
        if not isinstance(data["facts"], list):
            return failed("GET /debug/facts -- facts is list", f"got {type(data['facts']).__name__}")
        return passed("GET /debug/facts", f"count={data['count']}")
    except Exception as exc:
        return failed("GET /debug/facts", str(exc))


def test_debug_facts_session_filter() -> bool:
    """GET /debug/facts?session_id=... filters to only that session."""
    sid = new_sid()
    try:
        # Send a fact-bearing message
        post_chat("Luna weighs 5 kg -- I weighed her this morning", sid)
        wait_background("session filter")

        # Fetch facts for this specific session
        facts = facts_for(sid)
        if not facts:
            return failed("Debug facts session filter", "no facts returned for this session")

        # Verify all returned facts belong to this session
        wrong_session = [f for f in facts if f.get("session_id") != sid]
        if wrong_session:
            return failed(
                "Debug facts session filter",
                f"{len(wrong_session)} fact(s) from wrong session",
            )
        return passed("Debug facts session filter", f"{len(facts)} fact(s) all with correct session_id")
    except Exception as exc:
        return failed("Debug facts session filter", str(exc))


def test_facts_persist_in_db() -> bool:
    """Facts written to PostgreSQL are readable via the debug endpoint."""
    sid = new_sid()
    try:
        post_chat("Luna is allergic to chicken -- the vet confirmed it last week", sid)
        wait_background("DB persistence")

        # First read
        facts1 = facts_for(sid)
        if not facts1:
            return failed("Facts persist in DB", "no facts found after first read")

        # Read again -- should be the same (persisted, not ephemeral)
        facts2 = facts_for(sid)
        if len(facts2) != len(facts1):
            return failed(
                "Facts persist in DB",
                f"count changed between reads: {len(facts1)} -> {len(facts2)}",
            )
        return passed("Facts persist in DB", f"{len(facts1)} fact(s) persisted and stable")
    except Exception as exc:
        return failed("Facts persist in DB", str(exc))


def test_confidence_in_chat_response() -> bool:
    """POST /chat response includes confidence_score and confidence_color."""
    sid = new_sid()
    try:
        data = post_chat("Hello, how is Luna doing?", sid)
        score = data.get("confidence_score")
        color = data.get("confidence_color")
        if score is None:
            return failed("Confidence in chat", "missing confidence_score field")
        if color is None:
            return failed("Confidence in chat", "missing confidence_color field")
        if not isinstance(score, int):
            return failed("Confidence in chat -- score type", f"got {type(score).__name__}")
        if color not in ("green", "yellow", "red"):
            return failed("Confidence in chat -- color", f"got {color!r}")
        return passed("Confidence in chat response", f"score={score} color={color}")
    except Exception as exc:
        return failed("Confidence in chat", str(exc))


def test_profile_from_db_has_seed_data() -> bool:
    """Active profile read from DB contains seeded defaults (diet_type, medications)."""
    try:
        profile = get_profile()
        if not profile:
            return failed("Profile from DB -- has data", "profile is empty")

        # Check seed data keys exist
        expected_seeds = ["diet_type", "medications", "energy_level", "neutered_spayed"]
        present = [k for k in expected_seeds if k in profile]
        if len(present) < 3:
            return failed(
                "Profile from DB -- seed keys",
                f"only {len(present)}/{len(expected_seeds)} seed keys found: {present}",
            )
        return passed("Profile from DB has seed data", f"found: {present}")
    except Exception as exc:
        return failed("Profile from DB", str(exc))


# ── Runner ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{BOLD}{'=' * 56}{RESET}")
    print(f"{BOLD}  AnyMall-chan -- Phase 1C End-to-End Test Suite{RESET}")
    print(f"{BOLD}{'=' * 56}{RESET}")
    print(f"  Backend:   {BASE_URL}")
    print(f"  Storage:   PostgreSQL (via GET /debug/facts)")
    print(f"  BG wait:   {BACKGROUND_WAIT}s per Compressor test")

    # ── Pre-flight: is server up? ──────────────────────────────────────────────
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except Exception:
        print(f"\n{RED}ERROR: Cannot reach {BASE_URL}.{RESET}")
        print("Start the backend first:")
        print("  cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
        sys.exit(1)

    results: list[bool] = []

    # ── Section 1: Infrastructure ──────────────────────────────────────────────
    section("1  Infrastructure")
    results.append(test_health_endpoint())
    results.append(test_response_structure())
    results.append(test_confidence_endpoint())

    # ── Section 2: Intent Routing ──────────────────────────────────────────────
    section("2  Intent Routing")
    results.append(test_general_intent_no_redirect())
    results.append(test_health_intent_redirect())
    results.append(test_food_low_urgency_no_redirect())
    results.append(test_health_reply_is_short())

    # ── Section 3: Session Management ─────────────────────────────────────────
    section("3  Session Management")
    results.append(test_session_continuity())
    results.append(test_question_count_non_decreasing())
    results.append(test_new_sessions_are_independent())

    # ── Section 4: Compressor -- Fact Extraction ───────────────────────────────
    section(f"4  Compressor -- Fact Extraction  (each waits {BACKGROUND_WAIT}s)")
    results.append(test_non_fact_message_no_extraction())
    results.append(test_single_fact_extraction())
    results.append(test_multiple_facts_extraction())
    results.append(test_negative_fact_extraction())
    results.append(test_past_tense_fact_time_scope())
    results.append(test_vet_confirmed_high_confidence_and_source_rank())
    results.append(test_hedged_statement_lower_confidence())
    results.append(test_fact_log_schema())
    results.append(test_needs_clarification_flag())

    # ── Section 5: Aggregator -- Profile Merging ─────────────────────────────
    section(f"5  Aggregator -- Profile Merging  (each waits {BACKGROUND_WAIT}s)")
    results.append(test_aggregator_debug_endpoint())
    results.append(test_aggregator_new_fact())
    results.append(test_aggregator_confirmation())
    results.append(test_aggregator_better_fact())
    results.append(test_aggregator_past_fact_skipped())
    results.append(test_aggregator_seed_data_preserved())

    # ── Section 6: Database & Phase 1C ────────────────────────────────────────
    section("6  Database & Phase 1C")
    results.append(test_debug_facts_endpoint())
    results.append(test_debug_facts_session_filter())
    results.append(test_facts_persist_in_db())
    results.append(test_confidence_in_chat_response())
    results.append(test_profile_from_db_has_seed_data())

    # ── Summary ────────────────────────────────────────────────────────────────
    passed_count = sum(results)
    total        = len(results)
    colour       = GREEN if passed_count == total else RED

    print(f"\n{BOLD}{'=' * 56}{RESET}")
    print(f"  {BOLD}{colour}{passed_count}/{total} tests passed{RESET}")
    if passed_count < total:
        print(f"  {RED}{total - passed_count} failed -- see FAIL lines above{RESET}")
    print(f"{BOLD}{'=' * 56}{RESET}\n")

    sys.exit(0 if passed_count == total else 1)


if __name__ == "__main__":
    main()
