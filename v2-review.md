# v2 Suggested Questions — Code Review & Understanding Tracker

> Temporary file. Use this to read the code step-by-step, understand it, and mark off the review.
> Delete when done.

---

## What v2 Is (Read This First)

**v1** generated 4 questions per pet-combo, stored per pet-combo cache key.
If you had 2 pets there were 3 keys (pet A, pet B, both). One LLM call per combo.

**v2** generates 10 questions once, stored in one cache key per user per language.
The serving layer picks 3 out of 10 based on which module (food/health/anymall) is requested.

Key numbers to keep in your head:

| Thing           | Count |
|-----------------|-------|
| Questions generated at once | 10 |
| Questions shown to user | 3 |
| Cache keys per user | 1 (per language) |
| LLM calls per regen | 1 |
| Food slots | 4 (slots 0-3) |
| Health slots | 4 (slots 4-7) |
| Anymall slots | 2 (slots 8-9) |

The 10 questions each have two labels: `module` (food/health/anymall) and `target` (pet_a/pet_b/both).

---

## Files That Changed

| Step | File | What Changed |
|------|------|-------------|
| 1 | `backend/app/db/models.py` | Added `SuggestedQuestion` ORM model + `last_known_pet_ids` column on User |
| 2 | `backend/migrations/versions/f1a2b3...` | Alembic migration for new table |
| 3 | `backend/migrations/versions/a7b8c9...` | Alembic migration for `last_known_pet_ids` column |
| 4 | `backend/app/db/repositories.py` | Added `SuggestedQuestionsRepo` class |
| 5 | `backend/app/cache/keys.py` | Updated cache key format (no pet_ids, per-user-per-lang) |
| 6 | `backend/app/services/question_generation/validator.py` | Full rewrite: `validate_slot()` |
| 7 | `backend/app/services/question_generation/templates.py` | Full rewrite: 3-module EVERGREEN pools |
| 8 | `backend/app/agents/suggested_questions.py` | 4 → 10 questions, `module` field, new prompt layout |
| 9 | `backend/app/services/question_generation/generator.py` | NEW: `regen_for_user()` orchestration |
| 10 | `backend/app/routes/background.py` + `chat.py` | Use `regen_for_user`, v2 pick rule in /setup |
| 11 | `backend/app/jobs/nightly.py` | Use `regen_for_user` + `get_all_stale()` |
| 12 | `backend/app/routes/chat.py` | `/setup` gets `module` param, `_pick_questions` helper |
| 13 | `frontend/src/api.js` + `Chat.jsx` | Pass `module='anymall'` to fetchSetup |

---

## Step-by-Step Review

Mark each section [ ] → [x] as you read and understand it.

---

### Step 1 — Database Model
**File:** [backend/app/db/models.py](backend/app/db/models.py)
**Status:** [ ] understood  [ ] reviewed

**What was added:**
```
class SuggestedQuestion(Base):
    __tablename__ = "anymall_chan_suggested_questions"
    id            — auto int primary key
    user_code     — string, not null
    language      — string ("EN", "JA", etc.)
    questions     — JSON column (the 10-question list)
    generated_at  — timestamp (when LLM ran)
    created_at    — auto timestamp
    updated_at    — auto timestamp
    UNIQUE(user_code, language)  ← one row per user per language
```
Also on the `User` model:
```
last_known_pet_ids  — JSON column (list of ints)
```

**Why `last_known_pet_ids` on User?**
The nightly job needs to call `regen_for_user()` with pet IDs. But at midnight there is no HTTP
request — there are no pet IDs in scope. So we store the latest pet IDs used at chat time
and read them during the nightly batch.

**Review questions to think about:**
- What happens if a user gets a new pet between the last chat and the nightly run?
  (Answer: the nightly uses stale pet IDs; the next chat-triggered regen fixes it.)
- Why UNIQUE(user_code, language) and not just UNIQUE(user_code)?
  (Answer: same user may chat in EN and JA — they need separate question sets.)

---

### Step 2 & 3 — Alembic Migrations
**Files:**
- [backend/migrations/versions/f1a2b3c4d5e6_add_suggested_questions_table.py](backend/migrations/versions/f1a2b3c4d5e6_add_suggested_questions_table.py)
- [backend/migrations/versions/a7b8c9d0e1f2_add_last_known_pet_ids_to_users.py](backend/migrations/versions/a7b8c9d0e1f2_add_last_known_pet_ids_to_users.py)
**Status:** [ ] understood  [ ] reviewed

**What an Alembic migration is:**
Think of it as a version-controlled SQL script. `upgrade()` adds the table/column.
`downgrade()` removes it. Running `alembic upgrade head` applies all pending migrations in order.

**What to look for when reading:**
- Does `upgrade()` create the table with the right columns and constraints?
- Does `downgrade()` drop exactly what `upgrade()` added (nothing more, nothing less)?
- Is there a `UNIQUE` constraint on `(user_code, language)` in the suggested questions migration?

---

### Step 4 — Repository
**File:** [backend/app/db/repositories.py](backend/app/db/repositories.py)
**Status:** [ ] understood  [ ] reviewed

**What `SuggestedQuestionsRepo` does:**
```
upsert(user_code, language, questions, generated_at)
    → INSERT or UPDATE the row for (user_code, language)

get(user_code, language)
    → SELECT the row; returns None if not found

get_all_stale(age_days=7)
    → SELECT all rows where generated_at < now - 7 days
      AND last_known_pet_ids IS NOT NULL
    → Returns list of dicts for the nightly job to iterate
```

**Why IS NOT NULL guard in `get_all_stale`?**
If `last_known_pet_ids` is NULL we don't know which pets to regenerate for.
Calling `regen_for_user()` with an empty pet list would produce generic questions.
Better to skip and wait until the user chats again (which populates the field).

**Pattern to notice:** `upsert` uses PostgreSQL's `ON CONFLICT DO UPDATE` — this is the standard
safe way to "insert if not exists, update if exists" without a race condition.

---

### Step 5 — Cache Keys
**File:** [backend/app/cache/keys.py](backend/app/cache/keys.py)
**Status:** [ ] understood  [ ] reviewed

**v1 key format:** `am:suggested:{user_code}:{sorted_pet_ids}:{language}`
(e.g. `am:suggested:U123:101-102:JA`)

**v2 key format:** `am:suggested:{user_code}:{language}`
(e.g. `am:suggested:U123:JA`)

**Why the change?**
In v1, if you had pets 101 and 102, there were THREE cache keys:
- `am:suggested:U123:101:JA`
- `am:suggested:U123:102:JA`
- `am:suggested:U123:101-102:JA`

In v2, there is ONE key: `am:suggested:U123:JA`
The same 10 questions serve all pet-combo views. The pick rule at serve time filters by target.

**History key** (unchanged concept, new format):
`am:suggested_history:{user_code}:{language}` — stores the last 4 weeks of questions shown,
to prevent the LLM from repeating itself.

---

### Step 6 — Validator
**File:** [backend/app/services/question_generation/validator.py](backend/app/services/question_generation/validator.py)
**Status:** [ ] understood  [ ] reviewed

**Old v1:** `validate_questions(questions, language, pet_count, history)` — validated the whole list at once.
**New v2:** `validate_slot(question, slot_index, all_questions, language, pet_count, history)` — validates one slot.

**Why per-slot validation?**
If we validate the whole list and it fails, we'd discard everything. With per-slot we can:
patch only the bad slots with evergreen fallbacks and keep the good LLM-generated ones.

**8 validation rules (in order):**
1. `empty_text` — text is blank
2. `over_char_limit` — CJK max 36 chars, others max 56 chars
3. `duplicate_of_slot_N` — same text already in the 10 (case-insensitive)
4. `repeated_from_history` — was shown in last 4 weeks
5. `alarmist_content` — matches 14 clinical/emergency patterns
6. `invalid_module` — module not in {food, health, anymall}
7. `invalid_target` — target not in {pet_a, pet_b, both}
8. `single_pet_multi_pet_language` — text says "both pets"/"両方" but pet_count==1

**Return value:** `None` = valid. A short string = the failure reason.
The caller logs the reason and patches the slot.

---

### Step 7 — Templates (Evergreen Pools)
**File:** [backend/app/services/question_generation/templates.py](backend/app/services/question_generation/templates.py)
**Status:** [ ] understood  [ ] reviewed

**Structure:**
```python
EVERGREEN = {
    "food": {
        "EN": {
            "pet_a": [ {text, module, target}, ... ],  # 8 questions
            "pet_b": [ ... ],                           # 8 questions
            "both":  [ ... ],                           # 4 questions
        },
        "JA": { ... },
    },
    "health": { ... },
    "anymall": { ... },
}
```

**Fallback chain in `get_evergreen_questions(module, language, target, count)`:**
1. Try `EVERGREEN[module][language][target]`
2. If language not found → fall back to EN (not JA — EN is safer as a universal fallback)
3. If target not found → fall back to `pet_a`
4. Random sample from pool (so not always the same question)

**Why random sample?**
If we always picked index 0, users would see the same evergreen question every time the cache
is cold. Random sampling gives variety even from static templates.

**v1 vs v2 difference:**
v1 had `reason_type: "evergreen"` field — used to identify which questions were fallbacks.
v2 removed `reason_type`. The module/target labels replace it for all routing logic.

---

### Step 8 — Agent (LLM Prompt)
**File:** [backend/app/agents/suggested_questions.py](backend/app/agents/suggested_questions.py)
**Status:** [ ] understood  [ ] reviewed

**What changed from v1:**
- `pet_count` param removed, `all_pet_ids: list[int]` added
- `max_tokens` increased from 400 → 800 (10 questions need more tokens than 4)
- Prompt now has explicit slot layout:
  ```
  Slots 0-3: module="food"
  Slots 4-7: module="health"
  Slots 8-9: module="anymall"
    Slot 8: target must be "pet_a"
    Slot 9: target must be "both"
  ```
- Response format changed: `module` field instead of `reason_type`
- `_parse_response()` now checks for exactly 10 items (not 4)

**What `generate()` does:**
1. Formats the prompt with pet data, language, recent history
2. Calls `self._llm.complete()` with `temperature=0.9` (creative, not deterministic)
3. `_parse_response()` strips markdown fences → parses JSON → checks `len == 10` → validates each item has `text`, `module`, `target`
4. Returns the list (may be empty if LLM gave bad output — caller handles that)

**Why temperature=0.9?**
Questions should feel fresh and varied each time. Deterministic output (temp=0) would give
the same 10 questions every regen, which defeats the purpose of regenerating.

---

### Step 9 — Generator (Orchestration)
**File:** [backend/app/services/question_generation/generator.py](backend/app/services/question_generation/generator.py)
**Status:** [ ] understood  [ ] reviewed

**This is the heart of v2.** `regen_for_user()` wires everything together.

**Full pipeline (10 steps):**
```
1. For each pet_id → fetch pet profile from AALDA
2. Build pets_json (name/species/breed/age/sex) and trusted_context_json (high-confidence facts)
3. Load 4-week history from Valkey (am:suggested_history:{user_code}:{lang})
4. Call SuggestedQuestionsAgent.generate() → 10 questions
5. If LLM returned [] → _build_full_evergreen(language, pet_count) → fills all 10
6. Per-slot: validate_slot() → if invalid → get_evergreen_questions() → patch
7. Guard: if all 10 are empty after patching → log error and return (don't write garbage)
8. Write to Valkey (am:suggested:{user_code}:{lang})
9. Write to Postgres (SuggestedQuestionsRepo.upsert)
10. Trim history to 4 weeks + append this week's questions → write back to Valkey
```

**Important design choices:**
- `try/except Exception` wraps the whole thing — any error is logged and swallowed.
  The caller can safely fire-and-forget.
- Active profile: tried from Valkey first, falls back to DB. Keeps the main path fast.
- `jittered_ttl()` adds random ±10% to TTLs — prevents cache stampede at expiry.

**`_build_full_evergreen(language, pet_count)`:**
Used only when LLM returns empty. Fills 10 slots using evergreen pools.
For 1-pet users, `pet_b` slots get `pet_a` content (there is no second pet).

---

### Step 10 — Background Regen
**File:** [backend/app/routes/background.py](backend/app/routes/background.py)
**Status:** [ ] understood  [ ] reviewed

**What `_regen_suggested_questions` does (v2):**
```python
async def _regen_suggested_questions(user_code, pet_ids, state_bag):
    # 1. Get sq_agent, valkey, pet_fetcher from app state (fail fast if missing)
    # 2. Resolve language from am:user:{user_code} in Valkey (default "JA")
    # 3. Open DB session
    # 4. Create SuggestedQuestionsRepo
    # 5. Call regen_for_user(...)
```

**When is this called?**
After the aggregator processes new facts. If a high-confidence fact is extracted from the
chat (e.g. user mentioned Leo weighs 5kg), the aggregator busts the cache and triggers
this function as a fire-and-forget task.

**Why resolve language from Valkey here (not a param)?**
The chat pipeline doesn't know the language at the point it triggers background regen.
Language is stored in the user record in Valkey. We read it here to keep the caller simple.

---

### Step 11 — Nightly Job
**File:** [backend/app/jobs/nightly.py](backend/app/jobs/nightly.py)
**Status:** [ ] understood  [ ] reviewed

**What the nightly job does (v2):**
```python
async def _pregenerate_suggested_questions(app_state):
    # 1. Check sq_agent, vk, pet_fetcher are available (skip if any missing)
    # 2. Fetch all stale rows: generated_at < now - 7 days
    # 3. For each stale row:
    #    - asyncio.sleep(0.1)  ← rate limit between calls
    #    - Open DB session
    #    - regen_for_user(row["user_code"], row["last_known_pet_ids"], row["language"], ...)
```

**What "stale" means:**
A row is stale if `generated_at` is more than 7 days old. This means the user has questions
cached in Postgres but they're a week old — time to refresh them.

**Why `asyncio.sleep(0.1)` between users?**
The nightly job runs for ALL users at once. Without a rate limit, it would hammer AALDA
and the LLM API simultaneously. 0.1s spacing spreads the load.

**Why open a new DB session per user?**
SQLAlchemy async sessions are not thread-safe across long-lived operations. Opening one per
user ensures clean state and no connection leaks, even if one user's regen fails.

---

### Step 12 — /setup Endpoint (Pick Rule)
**File:** [backend/app/routes/chat.py](backend/app/routes/chat.py)
**Status:** [ ] understood  [ ] reviewed

**New query param:**
```
GET /api/v1/setup?pet_id=101&language=JA&module=anymall
```
`module` defaults to `"anymall"` if not provided. Pattern validated: `^(anymall|food|health)$`.

**`_pick_questions(all_questions, module, pet_count, language)` — the pick rule:**

For dual-pet (pet_count >= 2):
```
food    → food/pet_a  + food/pet_b  + food/both      (3 questions)
health  → health/pet_a + health/pet_b + health/both
anymall → food/pet_a  + health/pet_a + anymall/both
```

For single-pet (pet_count == 1):
```
food    → food/pet_a[0] + food/pet_a[1] + food/pet_a[2]
health  → health/pet_a[0] + health/pet_a[1] + health/pet_a[2]
anymall → food/pet_a + health/pet_a + anymall/pet_a
```

**Cache hit vs miss flow:**
```
Valkey hit  → parse JSON → _pick_questions → return 3
Valkey miss → Postgres get → warm Valkey → _pick_questions → return 3
Both miss   → _build_full_evergreen → _pick_questions → return 3
```

**Why `_build_full_evergreen` as the last resort?**
We never return an empty question list. Even if the user has never chatted before (cold
start), they see 3 generic questions from the evergreen pool.

---

### Step 13 — Frontend
**Files:**
- [frontend/src/api.js](frontend/src/api.js)
- [frontend/src/screens/Chat.jsx](frontend/src/screens/Chat.jsx)
**Status:** [ ] understood  [ ] reviewed

**`api.js` change:**
```javascript
// Before
export async function fetchSetup(petIds, userCode, language = 'auto') {

// After
export async function fetchSetup(petIds, userCode, language = 'auto', module = 'anymall') {
  ...
  const query = ids.map(id => `pet_id=${id}`).join('&') + `&language=${language}&module=${module}`
```

**`Chat.jsx` change:**
```javascript
// Before
fetchSetup(petIds, userCode, language)

// After
fetchSetup(petIds, userCode, language, 'anymall')
```

**Why hardcode `'anymall'` in Chat.jsx?**
Chat.jsx is the AnyMall-chan home screen. It always wants the anymall module mix.
The Food AI screen and Health AI screen would pass `'food'` or `'health'` when they call
fetchSetup (those screens don't exist yet — this is the hook for when they do).

---

## Code Review Checklist

Work through these after you understand each step above.

### Architecture
- [ ] Does every DB write go through a repo class? (no raw SQL in routes/agents)
- [ ] Does Valkey always have a Postgres fallback? (no hard dependency on cache)
- [ ] Are all LLM calls async? (no blocking calls in the async event loop)
- [ ] Does `regen_for_user` always catch exceptions? (fire-and-forget safety)

### Data integrity
- [ ] Does upsert use ON CONFLICT correctly? (no race condition on concurrent regen)
- [ ] Does `get_all_stale` guard against NULL `last_known_pet_ids`?
- [ ] Does the guard in `regen_for_user` prevent writing empty question lists?
- [ ] Does `_build_full_evergreen` handle 1-pet and 2-pet cases separately?

### Validation
- [ ] Are all 8 rules applied in `validate_slot`?
- [ ] Does the single-pet guard only trigger on the TEXT (not just the target field)?
- [ ] Is the alarmist pattern list complete? (14 patterns)
- [ ] Does the duplicate check skip comparing a slot to itself? (`if i == slot_index: continue`)

### Serving
- [ ] Does `_pick_questions` always return exactly 3 questions?
- [ ] Does the evergreen fallback in `_pick_questions` loop until it fills the count?
- [ ] Is the `module` param validated with a regex pattern in the route?
- [ ] Does the frontend correctly pass `module='anymall'` to fetchSetup?

### Tests
- [ ] Run `python -m pytest tests/test_suggested_questions.py -v`
- [ ] 54 tests should pass
- [ ] Check: are both the happy path AND edge cases covered for `validate_slot`?
- [ ] Check: does A8 (generator tests) mock Valkey AND Postgres?
- [ ] Check: does the cold-start test verify v2 structure (module/target/text)?

---

## Key Concepts to Lock In

**Cache-aside with write-through:**
Read from Valkey. On miss, read from Postgres and write back to Valkey.
On write, write to Postgres first (source of truth), then Valkey (hot cache).

**Fire-and-forget:**
Background regen is triggered with `asyncio.create_task()`. The user's HTTP response
does NOT wait for it. If regen fails, the user still gets their response. This is intentional.

**Universal cache key:**
One key per user per language. Not per pet combo. The 10 questions cover all pet views.
The pick rule at serve time filters the 3 questions the user actually sees.

**Slot layout is fixed:**
Slots 0-3 are ALWAYS food, 4-7 are ALWAYS health, 8-9 are ALWAYS anymall.
This is a contract between the LLM prompt and the pick rule. Changing one requires changing both.

**Evergreen = static fallback:**
Evergreen questions are hand-written in `templates.py`. They are never personalized.
They exist so the app never returns an empty question list, even before any LLM calls succeed.

---

## Test Run Command

```bash
cd backend
python -m pytest tests/test_suggested_questions.py -v
# Expected: 54 passed
```

---

*Last updated: 2026-03-29. Steps 1-13 complete.*
