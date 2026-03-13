# tests/test_pet_context.py
#
# Tests for the pet_context feature — sending real pet data from AALDA API
# instead of using hardcoded Luna defaults.
#
# Tests:
#   1. Without pet_context → still works (backward compatible, uses Luna)
#   2. With pet_context → agent responds about the real pet (Hana, not Luna)
#   3. With minimal pet_context (only required fields) → no crash
#   4. With "string" placeholder values from AALDA → mapped to "unknown" safely
#   5. _map_pet_context unit test → field mapping is correct
#
# Usage:
#   # Terminal 1 — start backend:
#   cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
#
#   # Terminal 2 — run tests:
#   cd backend && python tests/test_pet_context.py

import sys
import uuid

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

BASE_URL = "http://localhost:8000"
PASS_COUNT = 0
FAIL_COUNT = 0


def _post_chat(message: str, session_id: str, pet_context: dict | None = None) -> dict:
    """Send a POST /chat request and return the JSON response."""
    body = {
        "message": message,
        "session_id": session_id,
    }
    if pet_context is not None:
        body["pet_context"] = pet_context

    resp = requests.post(f"{BASE_URL}/chat", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _result(name: str, passed: bool, detail: str = "") -> bool:
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if passed else "FAIL"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    return passed


# ── Test 1: Backward compatibility — no pet_context ─────────────────────────

def test_no_pet_context():
    """Without pet_context, should still work and respond about Luna."""
    sid = f"test-noctx-{uuid.uuid4().hex[:8]}"
    data = _post_chat("How is my pet doing?", sid)

    ok = data.get("message") and len(data["message"]) > 10
    return _result(
        "Backward compat (no pet_context)",
        ok,
        f"Got {len(data.get('message', ''))} char reply" if ok else f"Bad response: {data}",
    )


# ── Test 2: With pet_context — agent should mention pet name ────────────────

def test_with_pet_context():
    """With pet_context for Hana the dog, agent should reference Hana (not Luna)."""
    sid = f"test-hana-{uuid.uuid4().hex[:8]}"
    pet = {
        "pet_id": 99,
        "name": "Hana",
        "species": "dog",
        "breed": "Golden Retriever",
        "birthday": "2023-05-20T00:00:00.000Z",
        "gender": "female",
    }
    data = _post_chat("How is Hana doing today?", sid, pet_context=pet)

    reply = data.get("message", "").lower()
    has_hana = "hana" in reply
    no_luna = "luna" not in reply

    return _result(
        "Real pet context (Hana)",
        has_hana and no_luna,
        f"reply mentions Hana={has_hana}, mentions Luna={not no_luna}",
    )


# ── Test 3: Minimal pet_context — only required fields ──────────────────────

def test_minimal_pet_context():
    """Only pet_id and name — should not crash, all other fields get defaults."""
    sid = f"test-minimal-{uuid.uuid4().hex[:8]}"
    pet = {
        "pet_id": 1,
        "name": "Mochi",
    }
    data = _post_chat("Tell me about my pet", sid, pet_context=pet)

    ok = data.get("message") and len(data["message"]) > 10
    return _result(
        "Minimal pet_context (id + name only)",
        ok,
        f"Got {len(data.get('message', ''))} char reply" if ok else f"Bad response: {data}",
    )


# ── Test 4: AALDA placeholder values ("string") ─────────────────────────────

def test_placeholder_values():
    """AALDA returns "string" as placeholder for unfilled fields. Should map to "unknown"."""
    sid = f"test-placeholder-{uuid.uuid4().hex[:8]}"
    pet = {
        "pet_id": 42,
        "name": "Taro",
        "species": "cat",
        "breed": "string",         # AALDA placeholder
        "birthday": "2024-01-01T00:00:00.000Z",
        "gender": "string",        # AALDA placeholder
    }
    data = _post_chat("How is Taro?", sid, pet_context=pet)

    reply = data.get("message", "").lower()
    ok = data.get("message") and len(data["message"]) > 10
    return _result(
        "AALDA placeholder 'string' values",
        ok,
        f"Got {len(data.get('message', ''))} char reply, no crash",
    )


# ── Test 5: Unit test _map_pet_context ───────────────────────────────────────

def test_map_pet_context_unit():
    """Test the field mapping logic without hitting the server."""
    # Import directly
    sys.path.insert(0, ".")
    from app.routes.chat import PetContext, _map_pet_context

    ctx = PetContext(
        pet_id=99,
        name="Hana",
        species="dog",
        breed="Golden Retriever",
        birthday="2026-03-13T05:17:12.981Z",
        gender="female",
    )
    result = _map_pet_context(ctx)

    checks = {
        "pet_id is string":        result["pet_id"] == "99",
        "name mapped":             result["name"] == "Hana",
        "species mapped":          result["species"] == "dog",
        "breed mapped":            result["breed"] == "Golden Retriever",
        "birthday to date_of_birth": result["date_of_birth"] == "2026-03-13",
        "gender to sex":            result["sex"] == "female",
    }

    all_ok = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    return _result(
        "_map_pet_context unit test",
        all_ok,
        f"Failed: {failed}" if failed else "All field mappings correct",
    )


# ── Test 6: Unit test — "string" placeholders mapped to "unknown" ───────────

def test_map_placeholder_unit():
    """AALDA "string" placeholders should become "unknown"."""
    sys.path.insert(0, ".")
    from app.routes.chat import PetContext, _map_pet_context

    ctx = PetContext(
        pet_id=1,
        name="Test",
        species="cat",
        breed="string",
        gender="string",
    )
    result = _map_pet_context(ctx)

    checks = {
        "breed 'string' to 'unknown'": result["breed"] == "unknown",
        "gender 'string' to 'unknown'": result["sex"] == "unknown",
    }

    all_ok = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    return _result(
        "Placeholder 'string' to 'unknown' mapping",
        all_ok,
        f"Failed: {failed}" if failed else "Placeholders mapped correctly",
    )


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Pet Context Tests ===\n")

    # Unit tests (no server needed)
    print("Unit tests:")
    test_map_pet_context_unit()
    test_map_placeholder_unit()

    # Integration tests (server must be running)
    print("\nIntegration tests (requires server at localhost:8000):")
    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except requests.ConnectionError:
        print("  [SKIP] Server not running. Start with: uvicorn app.main:app --port 8000")
        print(f"\nResults: {PASS_COUNT} passed, {FAIL_COUNT} failed (integration skipped)\n")
        sys.exit(1 if FAIL_COUNT > 0 else 0)

    test_no_pet_context()
    test_with_pet_context()
    test_minimal_pet_context()
    test_placeholder_values()

    print(f"\nResults: {PASS_COUNT} passed, {FAIL_COUNT} failed\n")
    sys.exit(1 if FAIL_COUNT > 0 else 0)
