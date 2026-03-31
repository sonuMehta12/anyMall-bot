# v2 Suggested Questions — Code Review Guide

> **Purpose:** Step-by-step guide for a code review agent.
> Goal: understand the code, confirm it matches the intended design, verify all logic
> and edge cases are correctly handled, and confirm end-to-end coverage.
>
> **IMPORTANT — Read this before starting:**
> The earlier version of this file described a "universal cache" design (one key per user).
> That design was **replaced** during implementation. The final system is **per-pet**.
> Everything in this file reflects the actual implemented code.

---

## The Mental Model (Read This First)

### What "per-pet" means

Every pet gets its **own** 10-question set, stored under its own key. A user with 2 pets
has 2 independent rows — one for each pet.

```
am:suggested:U123:EN:101   → 10 questions for Leo   (target="pet_a" on dedicated slots)
am:suggested:U123:EN:102   → 10 questions for Momo  (target="pet_b" on dedicated slots)
```

The serving layer (`/setup`) loads whichever pet(s) the user has selected, then picks 3
from the loaded row(s) based on the `module` param.

### Key numbers

| Thing | Value |
|-------|-------|
| Questions generated per pet per regen | 10 |
| Questions shown to user | 3 |
| Valkey keys per user (2 pets, EN) | 2 |
| LLM calls per regen | 1 per pet |
| Food dedicated slots | 0-2 (3 questions, target = this_pet) |
| Food both slot | 3 (1 question, target = "both") |
| Health dedicated slots | 4-6 (3 questions) |
| Health both slot | 7 |
| Anymall dedicated slot | 8 |
| Anymall both slot | 9 |

### The `is_pet_b` flag

Every pet row is generated with either `is_pet_b=False` (first pet) or `is_pet_b=True`
(second pet). This determines the `target` label on dedicated questions:
- `is_pet_b=False` → `target="pet_a"` on slots 0-2, 4-6, 8
- `is_pet_b=True`  → `target="pet_b"` on slots 0-2, 4-6, 8

**Why this matters:** When a user selects pet B alone (`?pet_id=102`), the system loads
the pet_b row and returns pet_b questions — not pet_a questions. Without this, selecting
pet B alone would show pet A's questions (Bug #2, now fixed).

### The cache-aside pattern (3-level fallback at serve time)

```
Step 1: Valkey hot cache   → hit?  serve it
Step 2: Postgres cold store → hit?  serve it + warm Valkey
Step 3: Evergreen fallback  → always available, generic text, NOT persisted
```

Cold-start evergreen rows are **built in memory only** — never written to Valkey or
Postgres. The real row gets written after the first background regen.

---

## What Changed vs the Old v2-review.md

The old file described:
- One cache key per user per language (❌ wrong — it's now per pet_id)
- `UNIQUE(user_code, language)` on the DB table (❌ wrong — now `UNIQUE(user_code, language, pet_id)`)
- `_pick_questions(all_questions, module, pet_count, language)` (❌ wrong signature)
- `regen_for_user` generating for all pets in one call (❌ wrong — one pet per call)
- 54 tests (❌ outdated — now 96 tests)

Everything below reflects the **actual code**.

---

## Files Changed — Full Map

| # | File | What changed |
|---|------|-------------|
| 1 | `backend/app/db/models.py` | `SuggestedQuestion` model + `pet_id` column + `UNIQUE(user_code,language,pet_id)` |
| 2 | `backend/migrations/versions/f1a2b3c4d5e6_...py` | Creates `anymall_chan_suggested_questions` table with `pet_id` |
| 3 | `backend/migrations/versions/a7b8c9d0e1f2_...py` | Adds `last_known_pet_ids` column to `anymall_chan_users` |
| 4 | `backend/app/db/repositories.py` | `SuggestedQuestionsRepo`: `get/upsert/get_all_stale` all carry `pet_id`; `cleanup_stale_language_rows()` added |
| 5 | `backend/app/cache/keys.py` | Key format is now `am:suggested:{user}:{lang}:{pet_id}` |
| 6 | `backend/app/services/question_generation/validator.py` | Per-slot `validate_slot()`, 8 rules |
| 7 | `backend/app/services/question_generation/templates.py` | Evergreen pools: 3 modules × EN/JA × pet_a/pet_b/both |
| 8 | `backend/app/agents/suggested_questions.py` | 10-question prompt, `is_pet_b` param, LLM 2-attempt retry |
| 9 | `backend/app/services/question_generation/generator.py` | `regen_for_user(... is_pet_b)` — single pet per call |
| 10 | `backend/app/routes/background.py` | `_regen_suggested_questions` loops per pet with `is_pet_b=(i==1)` |
| 11 | `backend/app/jobs/nightly.py` | Per-pet stale rows; `is_pet_b` from `last_known_pet_ids` position; `cleanup_stale_language_rows()` |
| 12 | `backend/app/routes/chat.py` | `_pick_questions(rows, pet_ids, module, language)`; `module` param; merge fix for `last_known_pet_ids` |
| 13 | `backend/tests/test_suggested_questions.py` | 96 tests, sections A0-A21 + B1-B4 |
| 14 | `backend/design-docs/suggested-questions-api.md` | NEW: frontend integration guide |

---

## Step-by-Step Review

Mark each section `[ ]` → `[x]` as you read and confirm it.

---

### Step 1 — Database Model
**File:** [backend/app/db/models.py](backend/app/db/models.py)
**Status:** [ ] read  [ ] confirmed

**`SuggestedQuestion` ORM model — what to verify:**
```
id            — auto int PK
user_code     — string, NOT NULL
language      — string ("EN", "JA", etc.), NOT NULL
pet_id        — integer, NOT NULL          ← per-pet key
questions     — JSON column (list of 10 dicts)
generated_at  — timestamp
UNIQUE(user_code, language, pet_id)        ← one row per user × lang × pet
```

**`User` model addition:**
```
last_known_pet_ids  — JSON column (list of ints, e.g. [101, 102])
```

**Things to confirm:**
- `pet_id` column exists on `SuggestedQuestion`
- Unique constraint includes `pet_id` (not just `user_code, language`)
- `last_known_pet_ids` is nullable (new users won't have it yet)

**Review question:** What happens if a user's pet is deleted and `last_known_pet_ids`
still contains the old pet_id?
> Expected answer: nightly job calls AALDA for the deleted pet_id → AALDA returns 404 →
> `regen_for_user` catches the exception, logs it, and moves on. The stale row stays
> in Postgres until cleanup.

---

### Step 2 & 3 — Migrations
**Files:**
- [backend/migrations/versions/f1a2b3c4d5e6_add_suggested_questions_table.py](backend/migrations/versions/f1a2b3c4d5e6_add_suggested_questions_table.py)
- [backend/migrations/versions/a7b8c9d0e1f2_add_last_known_pet_ids_to_users.py](backend/migrations/versions/a7b8c9d0e1f2_add_last_known_pet_ids_to_users.py)
**Status:** [ ] read  [ ] confirmed

**Things to confirm:**
- Migration 1 `upgrade()` creates the table with all 6 columns including `pet_id`
- Unique constraint in migration 1 covers `(user_code, language, pet_id)` — 3 columns
- Migration 1 `downgrade()` drops the table cleanly
- Migration 2 `upgrade()` adds `last_known_pet_ids` as nullable JSON to users table
- Migration 2 `downgrade()` drops only that column

---

### Step 4 — Repository
**File:** [backend/app/db/repositories.py](backend/app/db/repositories.py)
**Status:** [ ] read  [ ] confirmed

**`SuggestedQuestionsRepo` — three methods:**

```python
upsert(user_code, language, pet_id, questions, generated_at)
    → INSERT or UPDATE the row for (user_code, language, pet_id)
    → Uses PostgreSQL ON CONFLICT(user_code, language, pet_id) DO UPDATE

get(user_code, language, pet_id)
    → SELECT the row; returns dict or None

get_all_stale(age_days=7)
    → SQL:
        SELECT sq.user_code, sq.language, sq.pet_id, u.last_known_pet_ids
        FROM anymall_chan_suggested_questions sq
        JOIN anymall_chan_users u ON sq.user_code = u.user_code
        WHERE sq.generated_at < now() - interval '7 days'
          AND sq.language = u.preferred_language    ← only regen correct language
        ORDER BY sq.generated_at ASC
    → Returns list of dicts

cleanup_stale_language_rows()
    → DELETE FROM suggested_questions sq
      USING users u
      WHERE sq.user_code = u.user_code
        AND sq.language != u.preferred_language
    → Returns rowcount (int)
```

**Things to confirm:**
- `get_all_stale` SQL includes `AND sq.language = u.preferred_language` filter
  (prevents regenerating rows in an old language the user no longer uses)
- `upsert` ON CONFLICT targets all 3 columns: `(user_code, language, pet_id)`
- `cleanup_stale_language_rows` deletes rows where language does NOT match current preference
- `get_all_stale` returns `last_known_pet_ids` in the result dict (nightly job needs it)

**Review question:** Why does `get_all_stale` filter by `preferred_language`?
> If a user switches language from JA to EN, there is a JA row that is stale.
> Without the filter, the nightly job would regenerate it in JA — wasted LLM call.
> The filter ensures only the current-language row is regenerated.
> `cleanup_stale_language_rows` then deletes the old JA row after regen completes.

---

### Step 5 — Cache Keys
**File:** [backend/app/cache/keys.py](backend/app/cache/keys.py)
**Status:** [ ] read  [ ] confirmed

**Key formats:**
```
Suggested questions (per pet):
  am:suggested:{user_code}:{language}:{pet_id}
  e.g. am:suggested:U123:EN:101
       am:suggested:U123:EN:102    ← different key for pet 102

Suggestion history (per user, shared across pets):
  am:suggested_history:{user_code}:{language}
  e.g. am:suggested_history:U123:EN

Pattern for wildcard delete:
  am:suggested:{user_code}:*
```

**Things to confirm:**
- `CacheKeys.suggested_questions(user_code, lang, pet_id)` takes 3 args including `pet_id`
- Two different pet_ids produce two different keys for the same user
- History key does NOT include `pet_id` (history is shared: LLM shouldn't repeat any question
  regardless of which pet it was generated for)

---

### Step 6 — Validator
**File:** [backend/app/services/question_generation/validator.py](backend/app/services/question_generation/validator.py)
**Status:** [ ] read  [ ] confirmed

**`validate_slot(question, slot_index, all_questions, language, pet_count, history)`**
Returns `None` if valid, or a short reason string if invalid.

**8 rules in order:**
| # | Rule name | What it checks |
|---|-----------|----------------|
| 1 | `empty_text` | `q["text"]` is blank or missing |
| 2 | `over_char_limit` | CJK ≤ 36 chars, Latin ≤ 56 chars |
| 3 | `duplicate_of_slot_N` | Same text already exists at another slot (case-insensitive) |
| 4 | `repeated_from_history` | Text appeared in 4-week history |
| 5 | `alarmist_content` | Matches any of 14 clinical/emergency patterns |
| 6 | `invalid_module` | `module` not in `{food, health, anymall}` |
| 7 | `invalid_target` | `target` not in `{pet_a, pet_b, both}` |
| 8 | `single_pet_multi_pet_language` | `pet_count==1` but text contains "both pets", "two pets", "両方", etc. |

**Things to confirm:**
- Rule 3: when checking duplicates, it skips `i == slot_index` (doesn't compare slot to itself)
- Rule 8: triggers on the TEXT content, not just the `target` field
  (a question can have `target="both"` with innocuous text and still be valid for single-pet)
- `get_char_limit(language)` returns 36 for CJK scripts, 56 for Latin

---

### Step 7 — Evergreen Templates
**File:** [backend/app/services/question_generation/templates.py](backend/app/services/question_generation/templates.py)
**Status:** [ ] read  [ ] confirmed

**Structure:**
```python
EVERGREEN = {
    "food":    { "EN": { "pet_a": [...8], "pet_b": [...8], "both": [...4] },
                 "JA": { ... } },
    "health":  { ... },
    "anymall": { ... },
}
```

**`get_evergreen_questions(module, language, target, count)` fallback chain:**
1. Try `EVERGREEN[module][language][target]`
2. Language not found → fall back to `EN`
3. Randomly sample `count` items (no always-same-index)
4. Return deep copy (mutation-safe)

**Things to confirm:**
- `pet_b` evergreen pool uses "my other pet" / "my second pet" phrasing (not "my pet")
  — important: when pet B is selected alone, these would sound odd. Cold-start for pet B
  alone uses `pet_a` evergreen (sounds natural). This is intentional.
- All 3 modules have both EN and JA pools
- Return value is a new list (deep copy), not a reference to the EVERGREEN dict

---

### Step 8 — Agent (LLM Prompt)
**File:** [backend/app/agents/suggested_questions.py](backend/app/agents/suggested_questions.py)
**Status:** [ ] read  [ ] confirmed

**`generate(language, pet_id, pets_json, trusted_context_json, gap_list, recent_questions, model, is_pet_b)`**

**Prompt structure:**
```
System: You are generating suggested starter questions...
        SLOT LAYOUT — generate exactly 10 questions in this order:
        Slots 0-2: food, target={this_pet_target}
        Slot  3:   food, target="both"
        Slots 4-6: health, target={this_pet_target}
        Slot  7:   health, target="both"
        Slot  8:   anymall, target={this_pet_target}
        Slot  9:   anymall, target="both"
User:   Pet info: {pets_json}
        Trusted context: {trusted_context_json}
        Gaps: {gap_list}
        Recent questions: {recent_questions}
```

`this_pet_target` = `"pet_b"` if `is_pet_b=True`, else `"pet_a"`

**`_parse_response(raw)` — what it validates:**
1. Strip markdown fences (` ```json ... ``` `)
2. `json.loads()` — if fails, return `[]`
3. Check `len == 10` — if not, return `[]`
4. Check each item has non-empty `text`, valid `module`, valid `target`

**LLM retry logic (2 attempts):**
```python
for attempt in range(1, 3):
    raw = await self._llm.complete(...)
    result = self._parse_response(raw)
    if result:
        return result
    if attempt < 2:
        logger.warning("parse returned empty, retrying...")
# both failed → return []
```

**Things to confirm:**
- Retry only happens on empty parse result, NOT on `LLMProviderError` (network errors bail immediately)
- `is_pet_b` sets `this_pet_target` in the prompt so the LLM generates `target="pet_b"` on dedicated slots
- `max_tokens=800` (10 questions need more than the old 400 for 4 questions)

---

### Step 9 — Generator (Orchestration)
**File:** [backend/app/services/question_generation/generator.py](backend/app/services/question_generation/generator.py)
**Status:** [ ] read  [ ] confirmed

**`regen_for_user(user_code, pet_id, language, suggested_agent, suggested_repo, valkey, aalda_client, db_session, is_pet_b=False)`**

**Full pipeline (10 steps):**
```
1. Fetch pet profile from AALDA for this pet_id
2. Read active profile: Valkey first, fallback to Postgres
3. Build pets_json (name/species/breed/age/sex) and trusted_context_json (≥0.6 confidence facts)
4. Load 4-week history from Valkey (history key has no pet_id — shared across pets)
5. Call SuggestedQuestionsAgent.generate(is_pet_b=is_pet_b) → 10 questions
6. If LLM returned [] → _build_full_evergreen(language, is_pet_b=is_pet_b) fills all 10
7. Per-slot: validate_slot() → if invalid → patch with get_evergreen_questions()
8. Guard: if all 10 slots have empty text → log error, return (never write garbage)
9a. Write to Valkey: am:suggested:{user_code}:{language}:{pet_id}
9b. Write to Postgres: suggested_repo.upsert(..., pet_id=pet_id)
10. Update 4-week history in Valkey (trim to 4 weeks, append this week)
```

**Entire function is wrapped in `try/except Exception`** → caller can safely fire-and-forget.

**`_build_full_evergreen(language, is_pet_b=False)`:**
Builds 10 generic questions using evergreen pools with the correct `target` label.
```
tgt = "pet_b" if is_pet_b else "pet_a"
slots 0-2: food/tgt × 3
slot  3:   food/both × 1
slots 4-6: health/tgt × 3
slot  7:   health/both × 1
slot  8:   anymall/tgt × 1
slot  9:   anymall/both × 1
```

**Things to confirm:**
- `pet_id` is passed to `valkey.setex(CacheKeys.suggested_questions(user_code, language, pet_id), ...)`
- `pet_id` is passed to `suggested_repo.upsert(..., pet_id=pet_id, ...)`
- History key is `CacheKeys.suggested_history(user_code, language)` — NO pet_id
- The guard (step 8) checks `any(q.get("text") for q in final)` before writing

---

### Step 10 — Background Regen
**File:** [backend/app/routes/background.py](backend/app/routes/background.py)
**Status:** [ ] read  [ ] confirmed

**`_regen_suggested_questions(user_code, pet_ids, state_bag)`:**
```python
for i, pid in enumerate(pet_ids):
    async with get_session() as db_session:
        sq_repo = SuggestedQuestionsRepo(db_session)
        await regen_for_user(
            user_code=user_code,
            pet_id=pid,
            language=language,  # resolved from Valkey user cache
            ...
            is_pet_b=(i == 1),  # second pet in the list is pet_b
        )
```

**When is this triggered?**
In `_run_background()`, after the Aggregator merges high-confidence facts:
```python
if high and aggregator is not None:
    await asyncio.gather(*[_aggregate_one_pet(...) for ...])
    _create_tracked_task(
        _regen_suggested_questions(state.user_code, [p.id for p in state.pets], state_bag),
        state_bag,
    )
```
Condition: `high` is non-empty (at least one fact with confidence > 0.70).

**`pet_ids` passed in**: `[p.id for p in state.pets]` — the pets that were in the current chat.

**`is_pet_b` determination**: `i == 1` (loop index). Since `last_known_pet_ids` was just
updated in the same request with the same pet list, these are consistent.

**Things to confirm:**
- Language is resolved from Valkey user cache (not passed as parameter)
- Each pet opens its own DB session (not shared)
- `is_pet_b=(i==1)` means only the second pet in the list gets `pet_b` label
- This is called via `_create_tracked_task` — it runs AFTER the HTTP response is sent

---

### Step 11 — Nightly Job
**File:** [backend/app/jobs/nightly.py](backend/app/jobs/nightly.py)
**Status:** [ ] read  [ ] confirmed

**`_pregenerate_suggested_questions(app_state)`:**
```python
# 1. Check services available
if not (sq_agent and vk and pet_fetcher):
    return

# 2. Fetch all stale rows (7+ days old, matching preferred_language)
stale_rows = await sq_repo.get_all_stale()

# 3. For each stale row
for row in stale_rows:
    await asyncio.sleep(0.1)     # rate limit between LLM calls
    last_known = row["last_known_pet_ids"] or []
    pet_id     = row["pet_id"]
    is_pet_b   = (last_known.index(pet_id) == 1) if pet_id in last_known else False
    await regen_for_user(..., pet_id=pet_id, is_pet_b=is_pet_b)

# 4. After all rows, cleanup old-language rows
deleted = await sq_repo_cleanup.cleanup_stale_language_rows()
```

**`is_pet_b` determination in nightly** — uses `last_known_pet_ids.index(pet_id) == 1`.
This is the canonical source of truth (the DB), unlike background regen which uses loop index.

**Things to confirm:**
- `asyncio.sleep(0.1)` exists between loop iterations (rate limiting)
- Each row opens its own DB session (not reused)
- `cleanup_stale_language_rows()` is called AFTER all regen (not before)
- Nightly job catches and logs partial failures (one pet failing doesn't stop others)
- `import asyncio` is at the top of nightly.py (was missing, caused NameError — now fixed)

**Language cleanup flow:**
```
User changes language EN → JA:
  - Old EN rows remain in DB (stale)
  - get_all_stale() filters by preferred_language → only JA rows returned
  - EN rows never regenerated (no wasted LLM call)
  - cleanup_stale_language_rows() deletes EN rows after JA regen completes
```

---

### Step 12 — `/setup` Endpoint and Pick Rule
**File:** [backend/app/routes/chat.py](backend/app/routes/chat.py)
**Status:** [ ] read  [ ] confirmed

**Endpoint signature:**
```python
@router.get("/setup")
async def get_setup(
    request: Request,
    pet_id:   List[int] = Query(default=[]),
    language: str       = Query(default="auto"),
    module:   str       = Query(default="anymall", pattern="^(anymall|food|health)$"),
)
```

**Three-level question lookup (per pet_id):**
```
Step 1: for each pid in pet_id:
          key = CacheKeys.suggested_questions(user_code, lang, pid)
          row = await vk.get(key)
          if row: per_pet_rows[pid] = row["questions"]

Step 2: for missing_pids:
          pg_row = await sq_repo.get(user_code, lang, pid)
          if pg_row:
              per_pet_rows[pid] = pg_row["questions"]
              await vk.setex(key, ...)   ← warm Valkey

Step 3: for any pid still not in per_pet_rows:
          questions_cached = False
          per_pet_rows[pid] = _build_full_evergreen(lang, is_pet_b=(i==1))
```

**`_pick_questions(rows, pet_ids, module, language)` — the pick rule:**

```python
primary   = rows.get(pet_ids[0], [])
secondary = rows.get(pet_ids[1], []) if len(pet_ids) >= 2 else []

# Infer target labels from the actual questions in each row
_primary_dedicated   = [q for q in primary   if q.get("target") in ("pet_a","pet_b")]
primary_tgt   = _primary_dedicated[0]["target"]   if _primary_dedicated   else "pet_a"
_secondary_dedicated = [q for q in secondary if q.get("target") in ("pet_a","pet_b")]
secondary_tgt = _secondary_dedicated[0]["target"] if _secondary_dedicated else "pet_b"
```

Then:
```
module=food, single pet   → pick_from(primary, "food", primary_tgt, 3)
module=food, dual pet     → pick_from(primary, "food", primary_tgt)
                           + pick_from(secondary, "food", secondary_tgt)
                           + pick_from(primary, "food", "both")

module=health, single     → pick_from(primary, "health", primary_tgt, 3)
module=health, dual       → (same pattern as food)

module=anymall, single    → pick_from(primary, "food", primary_tgt)
                           + pick_from(primary, "health", primary_tgt)
                           + pick_from(primary, "anymall", primary_tgt)
module=anymall, dual      → pick_from(primary, "food", primary_tgt)
                           + pick_from(secondary, "health", secondary_tgt)
                           + pick_from(primary, "anymall", "both")
```

`pick_from` always returns exactly `n` items — fills remainder with evergreen if pool is short.

**Bug #1 (fixed):** `primary_tgt` was hardcoded as `"pet_a"`. When pet B was selected alone,
its row had `target="pet_b"` questions, but the pick rule looked for `target="pet_a"` → found
nothing → fell back to evergreen with wrong target. Fixed by inferring `primary_tgt` from
the actual questions in the row.

**`last_known_pet_ids` merge fix:**
```python
# Before (broken): overwrote the full list on every chat
"all_pet_ids": pet_ids

# After (fixed): merges, never shrinks
current_known = user_record.get("last_known_pet_ids") or []
merged_pet_ids = list(dict.fromkeys(current_known + pet_ids))
"all_pet_ids": merged_pet_ids
```

**Things to confirm:**
- `_pick_questions` infers `primary_tgt` from row content (not hardcoded "pet_a")
- `pick_from` fallback loop fills to exactly `n` — never returns fewer than `n`
- `module` regex pattern `^(anymall|food|health)$` is enforced in the Query param
- Merge logic: `dict.fromkeys(current_known + pet_ids)` preserves order and deduplicates
- `questions_cached=False` is set when ANY pet falls through to evergreen

---

### Step 13 — Tests
**File:** [backend/tests/test_suggested_questions.py](backend/tests/test_suggested_questions.py)
**Status:** [ ] read  [ ] confirmed

**Run command:**
```bash
cd backend && python tests/test_suggested_questions.py --unit
# Expected: 96 passed, 0 failed
```

**Section map:**

| Section | Tests | What it covers |
|---------|-------|---------------|
| A0 | 3 | Migration files exist + ORM model has `pet_id` column |
| A1 | 5 | Evergreen templates: all 3 modules, language fallback, deep copy |
| A2 | 11 | Validator: all 8 rules |
| A2b | 2 | `AgentState.all_pet_ids` field |
| A2c | 3 | `SuggestedQuestionsRepo` importable; `UserRepo` handles `all_pet_ids`; **merge never shrinks** |
| A3 | 5 | Cache key format (per-pet, includes `pet_id`) |
| A4 | 6 | `_parse_response`: 10 items, markdown fences, empty/malformed |
| A5 | 2 | Char limits: CJK=36, Latin=56 |
| A6 | 4 | Nightly staleness detection |
| A7 | 4 | Background regen: skips when services missing |
| A8 | 4 | `regen_for_user`: happy path, LLM empty → evergreen, slot patch |
| A9 | 3 | Language: EN vs JA produce separate keys |
| A10 | 2 | `is_pet_b` flag: `pet_a` vs `pet_b` target in stored questions |
| A11 | 2 | Background regen: 2 pets → 2 calls; `is_pet_b` set correctly per pet |
| A12 | 8 | `_pick_questions`: all module/pet-count combinations |
| A13 | 4 | Nightly: `get_all_stale` filters by language; cleanup exists |
| A14 | 1 | Two distinct Valkey keys written for 2 pets |
| A15 | 5 | Postgres repo: `pet_id` in `upsert`/`get` signatures |
| A16 | 3 | Language change: new key written, old key not served, nightly cleans up |
| A17 | 3 | `/setup` load path: single pet, **pet B alone**, dual pet |
| A18 | 2 | Cache HIT: Valkey served, Postgres never called |
| A19 | 3 | Postgres fallback: cold Valkey loads from DB, warms Valkey |
| A20 | 4 | Aggregator pipeline: high-confidence → regen; low-confidence → no regen |
| A21 | 4 | Nightly loop: one regen per row; `is_pet_b` from position; partial failure; empty rows |

**Integration tests (require live server):**
```bash
python tests/test_suggested_questions.py --integration
python tests/test_suggested_questions.py --module food
python tests/test_suggested_questions.py --module health
python tests/test_suggested_questions.py --module anymall
```

**Key edge-case tests to read carefully:**
- `test_setup_pet_b_alone_loads_pet_b_key_not_pet_a` (A17) — verifies Bug #1 fix
- `test_last_known_pet_ids_never_shrinks` (A2c) — verifies Bug #2 fix
- `test_language_change_old_language_key_not_served` (A16) — language switch scenario
- `test_nightly_pregenerate_is_pet_b_derived_from_last_known_pet_ids` (A21)
- `test_nightly_pregenerate_partial_failure_continues` (A21)

---

## Bugs Found and Fixed During This Session

### Bug #1 — Pet B selected alone showed Pet A questions

**Root cause:** `_pick_questions` hardcoded `primary_tgt = "pet_a"`. When pet B's row
(which has `target="pet_b"`) was loaded, `pick_from(primary, "food", "pet_a")` found no
matches → fell back to evergreen with `pet_a` target.

**Fix:** Infer `primary_tgt` from the first dedicated question in the actual row:
```python
_primary_dedicated = [q for q in primary if q.get("target") in ("pet_a", "pet_b")]
primary_tgt = _primary_dedicated[0]["target"] if _primary_dedicated else "pet_a"
```

**Test:** `test_setup_pet_b_alone_loads_pet_b_key_not_pet_a`

---

### Bug #2 — Single-pet chat overwrote the canonical pet list

**Root cause:** `chat.py` wrote `"all_pet_ids": pet_ids` on every chat. If user chatted
with `[102]` after previously having `last_known_pet_ids=[101, 102]`, the DB was overwritten
to `[102]`. Next nightly: pet 102 regenerated with `is_pet_b=False` → corrupted pet_b row.

**Fix:** Merge instead of overwrite:
```python
current_known = user_record.get("last_known_pet_ids") or []
merged_pet_ids = list(dict.fromkeys(current_known + pet_ids))
"all_pet_ids": merged_pet_ids
```

**Test:** `test_last_known_pet_ids_never_shrinks`

---

## Cold Start Behaviour (Common Question)

**Scenario:** Brand new user, no chat yet, calls `GET /setup?pet_id=102`.

1. Valkey miss (no key for pet 102)
2. Postgres miss (no row for this user yet)
3. Cold start: `_build_full_evergreen("EN", is_pet_b=(i==1))`
   - `i=0` (only pet in URL) → `is_pet_b=False` → `target="pet_a"` text
   - Text: "What's the best diet for my pet?" ← natural for single-pet view
4. `questions_cached=False` in response
5. Evergreen row is NOT persisted (memory only)

**Self-healing:** After user's first chat:
- `last_known_pet_ids` written to DB
- Background regen fires → writes real per-pet rows to Valkey + Postgres
- Next `/setup` call → `questions_cached=True`, personalised questions

**Edge case:** If user's first action is `/setup?pet_id=101&pet_id=102` (dual pet cold start):
- `i=0` for 101 → `pet_a` evergreen ✓
- `i=1` for 102 → `pet_b` evergreen ✓ (correct even on cold start for dual-pet URL)

---

## End-to-End Flow (Full Happy Path)

```
1. User has 2 pets [101, 102]. Opens Health AI.
   GET /setup?pet_id=101&pet_id=102&module=health
   → Valkey hit for both pets
   → _pick_questions: health/pet_a + health/pet_b + health/both
   → Response: 3 health questions, questions_cached=true

2. User chats: "Momo started limping yesterday"
   POST /chat with pet_ids=[101, 102]
   → chat.py merges last_known_pet_ids=[101,102] (already correct, no change)
   → Response returned to user immediately
   → Background: Compressor extracts fact {key:"limping", confidence:0.85, pet_label:"pet_b"}
   → Aggregator merges fact into Momo's active_profile
   → _regen_suggested_questions([101, 102]) fires as background task
     → regen_for_user(pet_id=101, is_pet_b=False) → new pet_a row written
     → regen_for_user(pet_id=102, is_pet_b=True)  → new pet_b row with Momo's limp context

3. User returns to Health AI seconds later.
   GET /setup?pet_id=101&pet_id=102&module=health
   → Valkey hit (regen already wrote the new rows)
   → Returns fresh questions: "How serious is limping in a [Momo's breed]?"
   → questions_cached=true, questions_generated_at=just now

4. Midnight — nightly job runs.
   → get_all_stale() finds rows older than 7 days
   → Skips [101, 102] (just regenerated, not stale)
   → Regenerates other users' stale rows
   → cleanup_stale_language_rows() deletes any old-language rows
```

---

## Architecture Checklist

Work through these after reading each step.

### Data flow
- [ ] Every DB write goes through a repo class (no raw SQL in routes/agents)
- [ ] Valkey always has a Postgres fallback in `/setup`
- [ ] Postgres write happens BEFORE Valkey write in `regen_for_user` (source of truth first)
- [ ] `regen_for_user` wraps everything in `try/except` (safe to fire-and-forget)

### Per-pet correctness
- [ ] `get/upsert` in `SuggestedQuestionsRepo` pass `pet_id`
- [ ] Valkey key includes `pet_id` (3-part key: user/lang/pet)
- [ ] `_pick_questions` infers `primary_tgt` from row content (not hardcoded "pet_a")
- [ ] `is_pet_b` is passed correctly to `regen_for_user` in both background and nightly
- [ ] `last_known_pet_ids` merge logic exists in `chat.py` (never shrinks)

### Language correctness
- [ ] `get_all_stale` filters by `preferred_language` (no wasted regen for old language)
- [ ] `cleanup_stale_language_rows` deletes wrong-language rows
- [ ] `/setup` resolves `language="auto"` from Valkey user cache

### Validation & fallback
- [ ] All 8 validation rules applied in `validate_slot`
- [ ] Failed slots patched with evergreen (not discarded)
- [ ] Guard in `regen_for_user` prevents writing all-empty question lists
- [ ] `_pick_questions` always returns exactly 3 (fills from evergreen if needed)

### Tests
- [ ] Run `python tests/test_suggested_questions.py --unit` → 96 passed, 0 failed
- [ ] Confirm A17 test 2 passes (pet B alone)
- [ ] Confirm A2c test 3 passes (merge never shrinks)
- [ ] Confirm A16 passes (language change full cycle)
- [ ] Confirm A21 test 3 passes (partial failure continues)
