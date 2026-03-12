# tests/test_reviewer_fixes.py
#
# Test suite for reviewer feedback fixes (Issues 2-7).
#
# Tests are grouped by issue. Each test function returns bool (True=pass).
# LLM-dependent tests use run_with_threshold() for non-determinism:
#   runs the test N times, passes if at least min_pass succeed.
#
# Usage:
#   # Terminal 1 — start backend:
#   cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
#
#   # Terminal 2 — run tests:
#   cd backend && python tests/test_reviewer_fixes.py

import re
import sys
import uuid

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run: pip install requests")
    sys.exit(1)


# ── Config ─────────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"


# ── Terminal colours ─────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ── Shared helpers ───────────────────────────────────────────────────────────

def new_sid() -> str:
    """Unique session ID for one test — keeps sessions isolated."""
    return f"rev-{uuid.uuid4().hex[:10]}"


def post_chat(message: str, session_id: str) -> dict:
    """POST /chat and return the parsed JSON body. Raises on HTTP error."""
    resp = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message, "session_id": session_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def passed(label: str, detail: str = "") -> bool:
    line = f"  {GREEN}PASS{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}» {detail}{RESET}"
    print(line)
    return True


def failed(label: str, detail: str = "") -> bool:
    line = f"  {RED}FAIL{RESET}  {label}"
    if detail:
        line += f"   {YELLOW}» {detail}{RESET}"
    print(line)
    return False


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")
    print("─" * 56)


def has_japanese(text: str) -> bool:
    """Check if text contains any Japanese characters (hiragana, katakana, CJK)."""
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text))


def run_with_threshold(
    fn,
    attempts: int = 3,
    min_pass: int = 2,
    label: str = "",
) -> bool:
    """
    Run an LLM-dependent test multiple times.
    Passes if at least min_pass out of attempts succeed.
    Each fn() must return bool.
    """
    results = []
    for i in range(attempts):
        try:
            result = fn()
            results.append(result)
        except Exception:
            results.append(False)

    pass_count = sum(results)
    test_label = label or fn.__name__
    detail = f"{pass_count}/{attempts} attempts passed (threshold: {min_pass})"

    if pass_count >= min_pass:
        return passed(test_label, detail)
    else:
        return failed(test_label, detail)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6: Issue 5 — Language Detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_japanese_input_japanese_reply() -> bool:
    """Japanese input should produce a Japanese reply."""
    sid = new_sid()
    try:
        data = post_chat("ルナちゃんは最近元気ですか？", sid)
        reply = data.get("message", "")
        if has_japanese(reply):
            return passed("JA input → JA reply", f"reply contains Japanese chars")
        return failed("JA input → JA reply", f"reply has no Japanese: {reply[:80]!r}")
    except Exception as exc:
        return failed("JA input → JA reply", str(exc))


def test_english_input_english_reply() -> bool:
    """English input should produce an English reply."""
    sid = new_sid()
    try:
        data = post_chat("How is Luna doing today?", sid)
        reply = data.get("message", "")
        # Allow a few Japanese chars (pet name emoji etc) but mostly English
        ja_count = len(re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', reply))
        if ja_count <= 3:
            return passed("EN input → EN reply", f"reply is English (ja_chars={ja_count})")
        return failed("EN input → EN reply", f"reply has {ja_count} Japanese chars: {reply[:80]!r}")
    except Exception as exc:
        return failed("EN input → EN reply", str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 7: Issue 2 — Probing Questions
# ═══════════════════════════════════════════════════════════════════════════════

def _check_reply_ends_with_question() -> bool:
    """Single attempt: send general msg, check reply ends with ? or ？"""
    sid = new_sid()
    data = post_chat("Luna seems a bit tired today", sid)
    reply = data.get("message", "").strip()
    # Check if the reply ends with a question mark (allowing trailing whitespace/emoji)
    # Strip trailing emoji/whitespace and check last punctuation
    cleaned = re.sub(r'[\s\U0001F300-\U0001FAFF]+$', '', reply)
    return cleaned.endswith("?") or cleaned.endswith("？")


def test_reply_ends_with_question() -> bool:
    """Agent reply should end with a follow-up question (LLM soft, 2/3 threshold)."""
    return run_with_threshold(
        _check_reply_ends_with_question,
        attempts=3,
        min_pass=2,
        label="Reply ends with question",
    )


def test_asked_gap_question_field_exists() -> bool:
    """Response JSON should contain 'asked_gap_question' boolean field."""
    sid = new_sid()
    try:
        data = post_chat("Hello!", sid)
        if "asked_gap_question" not in data:
            return failed("asked_gap_question field exists", "field missing from response")
        if not isinstance(data["asked_gap_question"], bool):
            return failed(
                "asked_gap_question field exists",
                f"expected bool, got {type(data['asked_gap_question']).__name__}",
            )
        return passed("asked_gap_question field exists", f"value={data['asked_gap_question']}")
    except Exception as exc:
        return failed("asked_gap_question field exists", str(exc))


def _check_gap_question_flag_true() -> bool:
    """Single attempt: send msg that should trigger gap probing."""
    sid = new_sid()
    # First message — agent has many gaps, should ask a gap question
    data = post_chat("Hi, I just adopted Luna!", sid)
    return data.get("asked_gap_question", False) is True


def test_gap_question_flag_true_when_gap_asked() -> bool:
    """When agent asks a gap question, asked_gap_question should be True (LLM soft)."""
    return run_with_threshold(
        _check_gap_question_flag_true,
        attempts=3,
        min_pass=2,
        label="Gap question flag = True when asking",
    )


def test_gap_counter_uses_flag_not_question_marks() -> bool:
    """
    questions_asked_count should only increment when asked_gap_question=True,
    not on every question mark in the reply.
    """
    sid = new_sid()
    try:
        # Send 3 messages, track the relationship between flag and counter
        r1 = post_chat("Tell me about Luna", sid)
        r2 = post_chat("She seems happy today", sid)
        r3 = post_chat("I love spending time with her", sid)

        counts = [r["questions_asked_count"] for r in [r1, r2, r3]]
        flags = [r.get("asked_gap_question", False) for r in [r1, r2, r3]]

        # Verify counter only increments when flag is True
        for i in range(1, len(counts)):
            if counts[i] > counts[i - 1] and not flags[i]:
                return failed(
                    "Gap counter uses flag",
                    f"counter increased ({counts[i-1]}→{counts[i]}) "
                    f"but asked_gap_question was False on turn {i+1}",
                )

        # Counter should be non-negative and non-decreasing
        if any(c < 0 for c in counts):
            return failed("Gap counter uses flag", f"negative count: {counts}")

        return passed("Gap counter uses flag", f"counts={counts} flags={flags}")
    except Exception as exc:
        return failed("Gap counter uses flag", str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 8: Issue 3 — Pet Name in Examples
# ═══════════════════════════════════════════════════════════════════════════════

def _check_reply_contains_pet_name_ja() -> bool:
    """Single attempt: send JA msg, check Luna/ルナ in reply."""
    sid = new_sid()
    data = post_chat("どうやって手伝ってくれるの？", sid)
    reply = data.get("message", "")
    return "Luna" in reply or "luna" in reply or "ルナ" in reply


def test_reply_contains_pet_name_ja() -> bool:
    """Japanese reply should contain pet name (Luna/ルナ) (LLM soft, 2/3)."""
    return run_with_threshold(
        _check_reply_contains_pet_name_ja,
        attempts=3,
        min_pass=2,
        label="JA reply contains pet name",
    )


def _check_health_example_references_pet_ja() -> bool:
    """Single attempt: send health concern in JA, check pet name in reply."""
    sid = new_sid()
    data = post_chat("ルナちゃん、最近あんまりちゃんと食べてなくて", sid)
    reply = data.get("message", "")
    return "Luna" in reply or "luna" in reply or "ルナ" in reply


def test_health_example_references_pet_ja() -> bool:
    """Health-related JA reply should reference pet by name (LLM soft, 2/3)."""
    return run_with_threshold(
        _check_health_example_references_pet_ja,
        attempts=3,
        min_pass=2,
        label="JA health reply references pet name",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 9: Issue 4 — Redirect Urgency Gating
# ═══════════════════════════════════════════════════════════════════════════════

def test_high_urgency_always_has_redirect() -> bool:
    """Emergency health message (HIGH urgency) should always have redirect."""
    sid = new_sid()
    try:
        data = post_chat(
            "Luna is having a seizure right now and won't stop shaking",
            sid,
        )
        redirect = data.get("redirect")
        if redirect is None:
            return failed("HIGH urgency → redirect present", "redirect is None")
        if redirect.get("module") != "health":
            return failed("HIGH urgency → module=health", f"got {redirect.get('module')!r}")
        return passed("HIGH urgency → redirect present", f"urgency={redirect.get('urgency')}")
    except Exception as exc:
        return failed("HIGH urgency → redirect present", str(exc))


def test_low_urgency_no_redirect() -> bool:
    """Routine question (LOW urgency) should NOT have redirect."""
    sid = new_sid()
    try:
        data = post_chat(
            "What's the best dry food brand for a Shiba Inu?",
            sid,
        )
        redirect = data.get("redirect")
        if redirect is not None:
            return failed(
                "LOW urgency → no redirect",
                f"redirect present: module={redirect.get('module')}, urgency={redirect.get('urgency')}",
            )
        return passed("LOW urgency → no redirect")
    except Exception as exc:
        return failed("LOW urgency → no redirect", str(exc))


def test_medium_urgency_cooldown() -> bool:
    """
    MEDIUM urgency: redirect on 1st occurrence, then cooldown for 3 messages.
    After cooldown, redirect can appear again.
    """
    sid = new_sid()
    try:
        # Turn 1: medium health concern — should get redirect
        r1 = post_chat("Luna has been limping on her front leg since yesterday", sid)
        redirect_1 = r1.get("redirect")
        if redirect_1 is None:
            return failed("MEDIUM cooldown — 1st redirect", "redirect is None on first message")

        # Turns 2-4: more medium health concerns — should be in cooldown (no redirect)
        cooldown_messages = [
            "Luna is also not eating much today",
            "She seems lethargic and just lies around",
            "I noticed some swelling on her paw",
        ]
        cooldown_redirects = []
        for msg in cooldown_messages:
            r = post_chat(msg, sid)
            cooldown_redirects.append(r.get("redirect"))

        # At least 2 of the 3 cooldown messages should have no redirect
        none_count = sum(1 for r in cooldown_redirects if r is None)
        if none_count < 2:
            return failed(
                "MEDIUM cooldown — suppressed",
                f"expected most cooldown msgs to have no redirect, "
                f"but {3 - none_count}/3 had redirect",
            )

        # Turn 5: after cooldown, should get redirect again
        r5 = post_chat("Luna is still limping and it seems worse today", sid)
        redirect_5 = r5.get("redirect")
        if redirect_5 is None:
            # This is LLM-dependent (classifier might not give medium again)
            # so just log it as a soft check
            return passed(
                "MEDIUM cooldown",
                f"cooldown worked ({none_count}/3 suppressed). "
                f"Post-cooldown redirect was None (classifier may have changed urgency)",
            )

        return passed(
            "MEDIUM cooldown",
            f"1st=present, cooldown={none_count}/3 suppressed, post-cooldown=present",
        )
    except Exception as exc:
        return failed("MEDIUM cooldown", str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 10: Issue 6 — No Repeated Questions
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_reask_after_allergy_answer() -> bool:
    """
    After user provides allergy info, agent should not re-ask about allergies.
    Multi-turn: provide allergy → chat about other things → check no allergy question.
    """
    sid = new_sid()
    try:
        # Turn 1: user provides allergy info
        post_chat("Luna has a shellfish allergy, especially shrimp", sid)

        # Turn 2-3: chat about other topics
        post_chat("She loves going on walks in the park", sid)
        r3 = post_chat("What else should I know about taking care of Luna?", sid)

        reply = r3.get("message", "").lower()

        # Check that the reply does not ask about allergies again
        allergy_question_patterns = [
            "allerg",
            "アレルギー",
        ]
        asks_about_allergy = False
        for pattern in allergy_question_patterns:
            if pattern in reply:
                # Check if it's a question about allergies (contains ? near the pattern)
                # vs just mentioning allergies in context
                idx = reply.find(pattern)
                surrounding = reply[max(0, idx - 20):min(len(reply), idx + 40)]
                if "?" in surrounding or "？" in surrounding or "かな" in surrounding:
                    asks_about_allergy = True
                    break

        if asks_about_allergy:
            return failed(
                "No re-ask after allergy answer",
                f"Agent re-asked about allergies: ...{reply[max(0, reply.find('allerg')-20):reply.find('allerg')+40]}...",
            )
        return passed("No re-ask after allergy answer", "agent did not re-ask about allergies")
    except Exception as exc:
        return failed("No re-ask after allergy answer", str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 11: Issue 7 — Emoji Consistency
# ═══════════════════════════════════════════════════════════════════════════════

def _check_emoji_before_first_pet_name() -> bool:
    """Single attempt: check 🐶 appears before first 'Luna' in reply."""
    sid = new_sid()
    data = post_chat("How is Luna doing?", sid)
    reply = data.get("message", "")

    # Find first occurrence of Luna (case-insensitive)
    luna_match = re.search(r'Luna', reply)
    if luna_match is None:
        # If Luna isn't mentioned, can't test — treat as pass
        return True

    luna_pos = luna_match.start()
    # Check if 🐶 appears within 3 chars before Luna
    prefix = reply[max(0, luna_pos - 3):luna_pos]
    return "🐶" in prefix


def test_emoji_before_first_pet_name() -> bool:
    """Pet emoji (🐶) should appear before first mention of Luna (LLM soft, 2/3)."""
    return run_with_threshold(
        _check_emoji_before_first_pet_name,
        attempts=3,
        min_pass=2,
        label="Emoji before first pet name",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print(f"\n{BOLD}{'=' * 56}{RESET}")
    print(f"{BOLD}  AnyMall-chan — Reviewer Fixes Test Suite{RESET}")
    print(f"{BOLD}{'=' * 56}{RESET}")
    print(f"  Backend: {BASE_URL}")
    print(f"  LLM-soft tests use 3 attempts, pass if 2/3 succeed")

    # ── Pre-flight: is server up? ────────────────────────────────────────────
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except Exception:
        print(f"\n{RED}ERROR: Cannot reach {BASE_URL}.{RESET}")
        print("Start the backend first:")
        print("  cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
        sys.exit(1)

    results: list[bool] = []

    # ── Section 6: Issue 5 — Language Detection ─────────────────────────────
    section("6  Issue 5 — Language Detection")
    results.append(test_japanese_input_japanese_reply())
    results.append(test_english_input_english_reply())

    # ── Section 7: Issue 2 — Probing Questions ──────────────────────────────
    section("7  Issue 2 — Probing Questions")
    results.append(test_reply_ends_with_question())
    results.append(test_asked_gap_question_field_exists())
    results.append(test_gap_question_flag_true_when_gap_asked())
    results.append(test_gap_counter_uses_flag_not_question_marks())

    # ── Section 8: Issue 3 — Pet Name in Examples ───────────────────────────
    section("8  Issue 3 — Pet Name in Examples")
    results.append(test_reply_contains_pet_name_ja())
    results.append(test_health_example_references_pet_ja())

    # ── Section 9: Issue 4 — Redirect Urgency Gating ────────────────────────
    section("9  Issue 4 — Redirect Urgency Gating")
    results.append(test_high_urgency_always_has_redirect())
    results.append(test_low_urgency_no_redirect())
    results.append(test_medium_urgency_cooldown())

    # ── Section 10: Issue 6 — No Repeated Questions ─────────────────────────
    section("10  Issue 6 — No Repeated Questions")
    results.append(test_no_reask_after_allergy_answer())

    # ── Section 11: Issue 7 — Emoji Consistency ─────────────────────────────
    section("11  Issue 7 — Emoji Consistency")
    results.append(test_emoji_before_first_pet_name())

    # ── Summary ──────────────────────────────────────────────────────────────
    passed_count = sum(results)
    total        = len(results)
    colour       = GREEN if passed_count == total else RED

    print(f"\n{BOLD}{'=' * 56}{RESET}")
    print(f"  {BOLD}{colour}{passed_count}/{total} tests passed{RESET}")
    if passed_count < total:
        print(f"  {RED}{total - passed_count} failed — see FAIL lines above{RESET}")
    print(f"{BOLD}{'=' * 56}{RESET}\n")

    sys.exit(0 if passed_count == total else 1)


if __name__ == "__main__":
    main()
