# Agent 3 (Aggregator) — Design Document

Plain thinking. All ideas, open questions, and decisions in one place.
Written before any code. Updated as decisions get made.

---

## 1. What the Aggregator Does (One Paragraph)

The Aggregator receives high-confidence facts from the Compressor and decides what to do
with each one. Its job is simple: compare each new fact against what we already believe
about the pet (the active_profile), apply a set of rules to determine which version of
the truth is better, and update the active_profile if the new fact wins. It never talks
to the user. It never calls an LLM. Every decision it makes is pure deterministic logic —
the same input will always produce the same output.

---

## 2. Why No LLM

The Compressor already did the hard work: it extracted a structured, typed fact from
natural language. The Aggregator receives `{key: "weight", value: "4 kg", confidence: 0.80,
source_rank: "explicit_owner", time_scope: "current", ...}` — a machine-readable object.

Deciding whether `confidence: 0.80` beats `confidence: 0.70` is arithmetic, not inference.
Deciding whether "today" is more recent than "3 weeks ago" is a timestamp comparison.
These decisions do not need a language model. Introducing an LLM here would:
- Add cost and latency to a hot path that runs on every chat message
- Introduce non-determinism where we need consistency
- Make debugging harder (why did this fact win? LLM explanation vs. rule trace)

The system design explicitly states: "Aggregator is NOT an LLM call — it's pure
deterministic application logic." The interface is designed so an LLM reasoning model
can replace this code later with zero changes to the surrounding pipeline if needed.

---

## 3. Where the Aggregator Lives in the Pipeline

```
POST /chat
    -> IntentClassifier (LLM)       health / food / general + urgency
    -> Agent 1 (LLM)                outputs {"reply": "...", "is_entity": bool}
    -> apply_guardrails()
    -> build_deeplink()
    -> Reply sent to user

    -> asyncio.create_task(_run_background(state))
         -> Compressor (LLM, temp=0.0)    extracts facts -> fact_log.json
         -> Aggregator (no LLM)           merges facts -> active_profile.json  ← NEW
```

The Aggregator receives a list of `ExtractedFact` objects — the ones Compressor produced
with confidence > 0.70 (already filtered by `_run_background`). It never sees low-confidence
facts. The Compressor's confidence threshold is the gate.

---

## 4. Storage: active_profile.json

### 4.1 Shape

The active_profile is a dict keyed by field name. Each key holds one entry — the
current best-known value for that field. This mirrors the future PostgreSQL
`active_profile` table (PK: `pet_id, key`).

```json
{
  "diet_type": {
    "value": "raw food",
    "confidence": 0.80,
    "source_rank": "explicit_owner",
    "time_scope": "current",
    "source_quote": "she's been on raw food since she was a puppy",
    "updated_at": "2025-01-15T10:30:00+00:00",
    "session_id": "abc-123"
  },
  "weight": {
    "value": "4",
    "confidence": 0.75,
    "source_rank": "explicit_owner",
    "time_scope": "current",
    "source_quote": "Luna weighs about 4kg",
    "updated_at": "2025-01-15T10:30:00+00:00",
    "session_id": "abc-123"
  }
}
```

### 4.2 Why This Shape

Every field is `{value, confidence, source_rank, time_scope, source_quote, updated_at,
session_id}` — exactly the same fields that exist in each ExtractedFact plus a timestamp.
This makes conflict resolution straightforward: every comparison is between two objects
of identical shape. No special-casing.

`updated_at` is the field we use for recency comparisons. It is set to UTC now() when
a fact wins and is written to active_profile.

`session_id` is kept so we can trace which conversation produced the current winning
value. Useful for debugging: "why does active_profile say Luna weighs 4kg?" → look up
that session_id in fact_log.

### 4.3 Where the File Lives

`backend/data/active_profile.json` — same `data/` directory as `fact_log.json`.
Created at first write. Gitignored (already handled by `data/` in .gitignore).

### 4.4 How It Relates to PostgreSQL (Phase 1C)

In Phase 1C, `write_active_profile()` and `read_active_profile()` in `file_store.py`
are swapped for PostgreSQL `UPSERT` and `SELECT` calls. The Aggregator code does not
change at all — it only calls those two functions. Same pattern as fact_log.

---

## 5. Conflict Resolution Rules

These rules are applied once per incoming fact, in priority order. First rule that
matches wins — remaining rules are not evaluated.

**Inputs per comparison:**
- `new` — an `ExtractedFact` from this Compressor run
- `current` — the entry in active_profile for `new.key`, or `None`

```
Rule 0: time_scope gate
    if new.time_scope == "past":
        → append to fact_log only (already done by _run_background)
        → do NOT touch active_profile
        → return

Rule 1: First-time key
    if current is None:
        → write new fact to active_profile
        → return

Rule 2: User explicit correction
    if new.source_rank == "user_correction":
        → overwrite active_profile unconditionally
        → return

Rule 3: Confirmation (same value, same key)
    if new.value == current.value:
        → boost current.confidence by min(current.confidence + 0.05, 1.0)
        → update current.updated_at to now
        → return

Rule 4: Low-confidence new fact
    if new.confidence < current.confidence * 0.80:
        → do nothing to active_profile (new fact too uncertain to win)
        → return
        (fact is already in fact_log — logged, but does not update profile)

Rule 5: New fact is better (newer or higher confidence)
    if new.confidence >= current.confidence * 0.80:
        → overwrite active_profile with new fact
        → return

Rule 6: True conflict (should not reach here often)
    → keep current in active_profile (do not overwrite)
    → log a warning with both values for debugging
    → return
```

### 5.1 Why 0.80 as the Confidence Threshold (Rule 4)

A new fact at 0.80× of the existing confidence is "close enough" to be worth updating.
Example: existing `weight` at confidence 0.90 (vet confirmed). New message says
"she's around 4kg" — confidence 0.72 (hedged). 0.72 < 0.90 × 0.80 = 0.72. Edge case —
it would not overwrite. That is the correct call: a casual mention should not beat a
vet-confirmed weight.

Opposite: existing `energy_level` at confidence 0.70 (inferred). New message says
"Luna is really tired today" — confidence 0.75 (observed). 0.75 >= 0.70 × 0.80 = 0.56.
It wins. Correct: a direct observation beats an inference.

### 5.2 source_rank Hierarchy

The `source_rank` field carries a rough quality signal from the Compressor:
- `"vet_record"` — user mentioned a vet, test result, or medical confirmation
- `"explicit_owner"` — owner stated directly
- `"user_correction"` — user explicitly corrected a fact ("actually she eats raw food")

In Phase 1B the confidence arithmetic handles most cases. `source_rank` is stored but
not used as a tie-breaker in Phase A — we rely on confidence + recency. This is
intentional simplicity. If the confidence arithmetic produces wrong decisions in practice,
source_rank is already in the data and can be added to the rules in Phase B.

### 5.3 The time_scope Rule

If the Compressor tagged a fact as `time_scope: "past"` (e.g., "Luna had ear infections
last year"), it should NOT overwrite the current state of `active_profile`. Example:
- Current: `current_conditions: "ear infection active"` (confidence 0.90, 3 days ago)
- New: `current_conditions: "ear infection"` (time_scope: "past", 14 months ago)

The past fact belongs in the audit trail (fact_log). It should not evict a current
observation from active_profile. Rule 0 handles this with a single check before
any other logic runs.

---

## 6. Sub-Phase Plan

We break the Aggregator into three sub-phases. Each one is complete and testable
on its own. Do not build Phase B until Phase A is done and confirmed working.

### Phase A — Core Merge (build first)

**What it does:**
- Read current `active_profile.json`
- Apply Rules 0–6 per incoming fact
- Write updated `active_profile.json` atomically

**What it does NOT do yet:**
- Gap list computation (Phase B)
- Confidence bar score (Phase C)

**New code required:**

| File | Change |
|---|---|
| `app/storage/file_store.py` | Already done — `read_active_profile()` and `write_active_profile()` exist |
| `app/agents/aggregator.py` | NEW — `AggregatorAgent` class, `run()` method |
| `app/main.py` | Add `_aggregator` global, wire into `_run_background()` after Compressor |

**`_run_background()` after Phase A:**
```
Compressor runs → extracts facts → fact_log written
Aggregator runs → takes high-confidence facts from state.extracted_facts
               → reads active_profile.json
               → applies rules per fact
               → writes active_profile.json
```

**New debug endpoint (Phase A):**
`GET /debug/profile` — returns the current `active_profile.json` contents.
Same pattern as `GET /debug/facts`. Used in browser console to verify Aggregator output.

**Verification after Phase A:**
```
Send: "Luna weighs about 4kg"
Wait 8 seconds
GET /debug/profile
→ active_profile["weight"] = {"value": "4", "confidence": 0.75, ...}

Send: "Luna weighs about 4kg" again (same session)
GET /debug/profile
→ active_profile["weight"]["confidence"] = 0.80 (boosted by Rule 3)

Send: "actually the vet confirmed she's exactly 4.2kg"
GET /debug/profile
→ active_profile["weight"] = {"value": "4.2", "confidence": 0.95, ...}
  (Rule 2 — user_correction wins unconditionally)
```

---

### Phase B — Gap List Computation (after Phase A confirmed working)

**What it does:**

After the Aggregator writes active_profile.json, it computes which fields are missing
or stale — the gap_list. This is compared against the full known field list (from
the PRD's Rank A/B/C/D classification).

Gap definition:
- Field is not in active_profile at all — "missing"
- Field is in active_profile but confidence < 0.60 — "low confidence"
- Field is in active_profile but updated_at > 30 days ago — "stale" (for transient
  fields like energy_level and appetite — not for stable facts like breed)

The gap_list is prioritised by rank (Rank A fields are asked first).

**What changes:**
- `AggregatorAgent.run()` returns a `gap_list: list[str]` alongside the updated profile
- `AgentState.gap_list` is set by Aggregator, passed to Agent 1
- Agent 1 uses gap_list to decide which 1–2 questions to ask naturally (it already
  receives gap_list from `context_builder.py` — Phase B makes this dynamic based on
  Aggregator output)

---

### Phase C — Confidence Bar Score (after Phase 1C PostgreSQL swap)

**What it does:**

After each Aggregator update, compute a single 0–100 score that represents how
complete and fresh the pet's profile is. This is the number shown in the Flutter
Confidence Bar UI.

**Formula (from system-design.md):**
```
score = Coverage × 0.4 + Recency × 0.3 + Depth × 0.3

Coverage = (filled Rank A+B fields) / (total Rank A+B fields)
Recency  = weighted average recency of all filled fields
           (facts updated < 7 days = 1.0, < 30 days = 0.7, < 90 days = 0.4, older = 0.1)
Depth    = average confidence score across all filled fields
```

**Why Phase C is deferred:**
The Confidence Bar is consumed by the Flutter mobile app. Until the API is connected
to a real pet (Phase 1C+), the score is meaningless — it would only reflect Luna's
hardcoded profile. Build it when there is a real profile to score.

**What changes in Phase C:**
- Aggregator returns `profile_confidence_score: int` (0–100)
- `ChatResponse` in main.py exposes `profile_confidence_score`
- Flutter reads this field and updates the Confidence Bar colour (green/yellow/red)

---

## 7. AggregatorAgent Interface

```python
class AggregatorAgent:
    """
    Pure rule-based fact aggregator. No LLM.
    Merges high-confidence facts from Compressor into active_profile.json.
    """

    def run(self, facts: list[ExtractedFact]) -> dict:
        """
        Merge a list of high-confidence facts into active_profile.json.

        Args:
            facts: High-confidence ExtractedFact objects from Compressor
                   (caller has already filtered to confidence > 0.70).

        Returns:
            The updated active_profile dict after all merges.
        """
```

No `__init__` params needed — Aggregator has no LLM, no external dependency.
It calls `read_active_profile()` and `write_active_profile()` from file_store.

---

## 8. file_store.py — Already Done

`read_active_profile()` and `write_active_profile()` already exist in `file_store.py`
(added during the data model refactor). Same atomic `.tmp → os.replace()` pattern.

`read_active_profile()` returns `None` when the file does not exist (not `{}`).
The Aggregator must handle `None` → treat as empty profile (all facts are Rule 1).

These are the only two entry points the Aggregator uses. In Phase 1C both functions
are replaced by PostgreSQL UPSERT and SELECT — the Aggregator code does not change.

---

## 9. What Changes in `_run_background()` (Phase A)

Current (Compressor only):
```python
async def _run_background(state: AgentState) -> None:
    facts = await _compressor.run(state)
    high = [f for f in facts if f.confidence > 0.70]
    low  = [f for f in facts if 0.50 <= f.confidence <= 0.70]
    state.extracted_facts = high
    state.low_confidence_fields = [f.key for f in low]
    if facts:
        # ... write to fact_log.json
```

After Phase A (Compressor + Aggregator):
```python
async def _run_background(state: AgentState) -> None:
    facts = await _compressor.run(state)
    high = [f for f in facts if f.confidence > 0.70]
    low  = [f for f in facts if 0.50 <= f.confidence <= 0.70]
    state.extracted_facts = high
    state.low_confidence_fields = [f.key for f in low]
    if facts:
        # ... write to fact_log.json (unchanged)

    # NEW — merge high-confidence facts into active_profile
    if high:
        _aggregator.run(high)
```

Nothing else in the request/response cycle changes. The user still gets their reply
before any of this runs.

---

## 10. Known Limitations of Phase A (Intentional)

**No per-pet namespacing.** Phase A stores a single `active_profile.json` for the one
hardcoded pet (Luna). In Phase 1C with real users, the profile will be keyed by pet_id
in PostgreSQL. The JSON shape already uses field-keyed dicts (not pet_id-keyed) which
is consistent with how the PostgreSQL table will store one row per (pet_id, key).

**No persistence of gap_list.** Gap list is computed from scratch in Phase B each time.
No stale data problem.

**No conflict flagging UI.** Rule 6 (true conflict) logs a warning but there is no
admin endpoint to review flagged conflicts. That is fine for Phase 1B — there is one
hardcoded pet, one developer. Add a `GET /debug/conflicts` endpoint in Phase 1C when
there are real users.

**Transient fields (energy_level, appetite) never decay automatically.** In Phase A,
once written to active_profile, they stay until overwritten by a new observation.
True decay (e.g., energy_level expires after 24 hours unless re-confirmed) requires
either a scheduled job or timestamp checks at read time. Deferred to Phase B when
gap logic is built.

---

## 11. Open Questions (to resolve before building)

1. **source_rank: "user_correction" detection** — the Compressor sets `source_rank`
   based on the message content. Does the current Compressor prompt reliably detect
   correction phrases ("actually", "no, it's", "I was wrong — she eats")? Test a few
   messages before assuming this works.

2. **`updated_at` in ExtractedFact** — ExtractedFact has a `timestamp` field which is
   only set if the user explicitly states a time. For Aggregator recency comparisons,
   we need `updated_at` (when the fact was extracted), not `timestamp` (a time the
   user mentioned). Use `extracted_at` from fact_log (added by main.py at write time).
   Decision: Aggregator reads `extracted_at` from the fact dict when comparing recency,
   not `timestamp`.

3. **What happens when Aggregator crashes?** It runs inside `_run_background()` which
   already has a try/except that logs and swallows errors. The user is unaffected.
   fact_log.json is written BEFORE Aggregator runs — so facts are never lost even if
   Aggregator fails. This is already the right design.

4. **First run — active_profile.json does not exist yet.** `read_active_profile()`
   returns `{}`. Every fact is treated as Rule 1 (first-time key). All facts are written.
   This is correct behaviour — no special handling needed.

---

## 12. Reused Patterns

| Pattern | From | Used in |
|---|---|---|
| Atomic write (.tmp → os.replace) | `file_store.py:append_fact_log` | `write_active_profile` |
| Module-level `logger = logging.getLogger(__name__)` | Every module | `aggregator.py` |
| Global singleton at startup | `_compressor` in `main.py` | `_aggregator` in `main.py` |
| try/except + log in background task | `_run_background` | Aggregator call inside same function |
| `ExtractedFact` as input type | `compressor.py` | `aggregator.py:run()` signature |

---

## 13. Data Model (Implemented)

Three dataclasses in `app/models/context.py`, each mirroring a future PostgreSQL table.
JSON files in `data/` are the Phase 1B storage. `context_builder.py` reads them every
request and returns the 5 values Agent 1 needs.

### 13.1 PetProfile — `data/pet_profile.json`

Static pet identity, set at onboarding. Rarely updated by the Aggregator.

```python
@dataclass
class PetProfile:
    pet_id: str           # "luna-001"
    name: str             # "Luna"
    species: str          # "dog" | "cat"
    breed: str            # "Shiba Inu"
    date_of_birth: str    # ISO date "2024-01-15" or "unknown"
    sex: str              # "male" | "female" | "unknown"
    life_stage: str       # "puppy" | "adult" | "senior"
```

### 13.2 ActiveProfileEntry — `data/active_profile.json`

One dynamic fact per key. The Aggregator creates/updates these entries.
JSON file shape: `{"field_name": {value, confidence, ...}, "_pet_history": "..."}`.

```python
@dataclass
class ActiveProfileEntry:
    key: str              # snake_case field name (matches FULL_FIELD_LIST)
    value: str            # always a string, units normalized
    confidence: float     # 0.0–1.0
    source_rank: str      # "vet_record" | "explicit_owner" | "user_correction"
    time_scope: str       # "current" | "past" | "unknown"
    source_quote: str     # exact user text that supports this fact
    updated_at: str       # ISO datetime — when this entry was written
    session_id: str       # which conversation produced this value
    status: str           # "new" | "updated" | "confirmed"
    change_detected: str  # "" or "decreased_from_moderate_to_low"
    trend_flag: str       # "" or "declining_energy"
```

**status / change_detected / trend_flag** — PRD conflict resolution fields.
Set by the Aggregator when applying Rules 0–6:
- Rule 1 (first-time) → status="new"
- Rule 3 (confirmation) → status="confirmed"
- Rule 5 (better fact) → status="updated", change_detected="old_value → new_value"

### 13.3 UserProfile — `data/user_profile.json`

Owner relationship data. In Phase 1B, hardcoded (seeded on first run).

```python
@dataclass
class UserProfile:
    user_id: str                  # "shara-001"
    pet_id: str                   # "luna-001"
    session_count: int            # 7
    relationship_summary: str     # NL text for Agent 1
    updated_at: str               # ISO datetime
```

### 13.4 How context_builder.py Uses These

```
build_context() → (active_profile_dict, gap_list, pet_summary, pet_history, relationship_context)

1. Read pet_profile.json       → seed Luna defaults if missing
2. Read active_profile.json    → seed dynamic entries if missing
3. Read user_profile.json      → seed Shara defaults if missing
4. Merge pet_profile static fields into active_profile as high-confidence entries
5. Compute gap_list = FULL_FIELD_LIST − present keys − identity fields
6. Compute pet_summary from template (no LLM)
7. Return 5-tuple — Agent 1 receives this every request
```

Identity fields (`name`, `species`, `breed`, `age`, `sex`) are excluded from gap_list
because they are always known from onboarding.
