# AnyMall-chan — Build Journal

Plain-language notes on what we built, why, and what comes next.
No jargon. Written so you can read this after a week away and know exactly where you are.

---

## Phase 0 — Completed ✓

### What we built

A single chat endpoint. You send a message, you get a reply from Agent 1.
That is the entire thing. Nothing more, nothing less.

**The core loop:**
```
User sends message
    -> Agent 1               builds a prompt with pet context, calls Azure OpenAI
    -> apply_guardrails()    cleans the reply with regex (free, instant)
    -> User gets reply
```

### What Agent 1 knows (hardcoded for now)

We are just prototyping. There is no database yet. All pet data was originally hardcoded in
`dummy_context.py` (now replaced by `context_builder.py` + JSON files) — two fake characters we made up:

- **Luna** — 2-year-old Shiba Inu, raw food diet, currently on antibiotics for an ear infection
- **Shara** — Luna's owner, tends to be anxious, prefers short replies

Agent 1 receives this context on every request and uses it to give personalised replies.
The user never has to tell Agent 1 about Luna — it already knows.

### What Agent 1 receives (5 things)

1. **Active profile** — structured dict of known facts with confidence scores
2. **Gap list** — fields we don't know yet (weight, allergies, etc.)
3. **Pet summary** — one paragraph describing who Luna is right now (plain text)
4. **Pet history** — one paragraph of what happened to Luna across past sessions (plain text)
5. **Relationship context** — one sentence about Shara's communication style (plain text)

The system prompt is a template with placeholders — you can read the whole prompt in one
place at the top of `conversation.py`. No hunting through helper methods.

### How session memory works

Pure in-memory Python dict. `session_id -> list of messages`.
Agent 1 sees the full conversation history on every request so it "remembers" earlier messages.
Resets when the server restarts. This is intentional for Phase 0 — Redis comes in Phase 2.

### How the LLM provider works

We never hardcode "Azure" into the agent. The agent just calls `self._llm.complete()`.
Which LLM it talks to is decided by one env var: `LLM_PROVIDER=azure` in `.env`.
To switch to direct OpenAI later: change that one env var. Zero code changes in the agent.

### Files we wrote

| File | What it does |
|---|---|
| `constants.py` | Business logic constants — priority ranks, regex patterns, keyword lists, field labels |
| `app/core/config.py` | Reads `.env` -> typed `settings` object. One import away from anywhere. |
| `app/llm/base.py` | Abstract `LLMProvider` contract — 2 methods: `complete()` and `health_check()` |
| `app/llm/azure_openai.py` | Azure OpenAI implementation of LLMProvider |
| `app/llm/factory.py` | Reads `settings.llm_provider` -> creates the right provider |
| `dummy_context.py` | Hardcoded Luna + Shara data. DELETED — replaced by `context_builder.py`. |
| `app/services/guardrails.py` | `apply_guardrails()` — regex only, no LLM |
| `app/agents/conversation.py` | Agent 1 — prompt template + `run()` |
| `app/main.py` | FastAPI app — `GET /health` + `POST /chat` |

### Bugs we found and fixed

- Import inside a function in `guardrails.py` — moved to top
- Duplicate `import sys` in `main.py` — cleaned up
- Guardrail regex was re-compiled on every request — now pre-compiled at startup
- `allow_credentials=True` with `allow_origins=["*"]` violates CORS spec — fixed to `False`

### What we deliberately left out (intentional simplicity)

- No database — pet data is hardcoded
- No Redis — session history is in RAM
- No Agent 2 or Agent 3 — no fact extraction, no fact aggregation
- No auth — anyone with the URL can call the API
- No rate limiting — can be spammed
- No tests — verified manually via Postman + React UI

All of the above are tracked in `design-docs/security.md` with the exact phase they get fixed.

---

## Phase 1A — Completed ✓

### What we built

Added a new node before Agent 1 — the **IntentClassifier** — and wired up the redirect
(deeplink) logic so health and food messages get handled differently instead of always
giving a generic response.

**Updated pipeline:**
```
User sends message
    -> IntentClassifier (LLM)    figures out health / food / general + urgency level
    -> Agent 1                   gets intent injected into prompt, calls Azure OpenAI
    -> apply_guardrails()        cleans the reply
    -> build_deeplink()          health or food? builds redirect payload for mobile app
    -> User gets reply + redirect button (if health or food)
```

### Why we added IntentClassifier

The old approach used regex to detect health/food intent. Regex is dumb — it cannot tell:
- "Luna is vomiting" (real concern) vs "Luna is NOT vomiting anymore" (resolved — not a concern)
- "Luna had a seizure last year but she's fine now" (past event, not current)
- "The vet said everything looks great" (positive outcome — not a concern)

All three would have fired the health flag with regex. Replaced it with a tiny LLM call
(temperature=0.0, max_tokens=48) that actually understands context. Costs almost nothing.
Gets it right every time.

### Why IntentClassifier is a separate node and not part of Agent 1

Agent 1 needs the intent injected into its system prompt BEFORE it generates a reply. You
cannot classify intent and generate a reply in the same LLM call — chicken and egg. Small
separate call first, then use the result to build Agent 1's prompt.

Retry policy: bad JSON or low confidence -> retry once. API error -> fall back to general
immediately. Max 2 attempts. Always returns something safe.

### What we removed (Option B — the big cleanup)

`IntentFlags` dataclass and `classify_intent()` regex function — both deleted from `guardrails.py`.
Entity extraction belongs in Agent 2, not here. `guardrails.py` now does one thing only: clean
Agent 1's output of blocked jargon and preachy phrases.

### The redirect / deeplink logic

When intent is `health` or `food`, the API response includes a `redirect` object:
```json
{
  "redirect": {
    "module": "health",
    "deep_link": "http://localhost:8000/health/chat?...",
    "pre_populated_query": "Luna has been vomiting since morning",
    "pet_summary": "Luna is a 2-year-old Shiba Inu...",
    "urgency": "high"
  }
}
```
Mobile app reads this and shows a redirect button (red for high urgency, orange for medium).
Simulator pages at `/health/chat` and `/food/chat` let us test this during development.

### How Agent 1 knows to hold back for health messages

A `THIS MESSAGE FLAGS:` block is injected into Agent 1's system prompt above the RULES section.
For health it says: "Empathy only. No advice, no diagnosis, 1-2 sentences, signal help is on the way."
RULES 7 and 8 reference "flags above" to enforce this. Agent 1 sees both and constrains itself.

### Bugs found and fixed in Phase 1A

- **"flags below" vs "flags above"** — RULES 7 and 8 said "if HEALTH INTENT DETECTED appears in
  the flags below" but the flag section renders ABOVE the rules in the prompt template. The LLM
  looked below, found nothing, and ignored the instruction entirely. Fixed to "flags above".
- **Zombie process on port 8000** — A dead Python process was holding port 8000 from a previous
  session. New server appeared to start fine but received zero requests. No logs, old behavior in
  the UI. Fixed by rebooting. Lesson: if you see no logs after startup, run
  `netstat -ano | findstr :8000` to check what owns the port.

### Files changed in Phase 1A

| File | What changed |
|---|---|
| `app/agents/intent_classifier.py` | NEW — LLM classifier, retry logic, graceful fallback |
| `app/agents/conversation.py` | Removed IntentFlags, added `intent_type` str, `_build_flag_section()` |
| `app/services/guardrails.py` | Deleted `IntentFlags`, `classify_intent()`, all entity regex logic |
| `app/services/deeplink.py` | Signature changed from IntentFlags to `(intent_type, urgency)` strings |
| `app/main.py` | Wired IntentClassifier into chat route, cleaned ChatResponse model |
| `constants.py` | Removed dead keyword lists (LLM handles urgency/food detection now) |

---

## Phase 1B — Compressor Complete ✓ | Aggregator Pending

### What we built

The Compressor (Agent 2) — fact extraction pipeline that runs in the background
after every chat reply. Full 18-test automated suite passing.

**Updated pipeline:**
```
User sends message
    → IntentClassifier (LLM)       health / food / general + urgency
    → Agent 1 (LLM)                outputs JSON: {"reply": "...", "is_entity": true/false}
    → apply_guardrails()           cleans reply
    → build_deeplink()             health/food: builds redirect payload
    → Reply sent to user ✓

    → asyncio.create_task(_run_background)    ← user does NOT wait
         → Compressor (LLM, temp=0.0)        extracts facts → fact_log.json
```

### Key decisions made

**is_entity gate (Option C) — zero extra LLM cost:**
Agent 1 and the entity gate are ONE LLM call. Agent 1 now outputs
`{"reply": "...", "is_entity": true|false}`. If `is_entity=false`, Compressor exits
immediately — no LLM call. We considered Option A (regex pre-filter) and Option B
(separate LLM call) and chose C: same cost, same calls, flag comes for free.

**AgentState — plain dataclass, no framework:**
A 25-line Python dataclass that carries context through the background pipeline.
No LangGraph, no framework needed — the pipeline is linear with one background branch.
Fields: session_id, user_message, pet essentials, recent_history, is_entity,
extracted_facts, low_confidence_fields.

**Confidence thresholds:**
- `> 0.70` → high-confidence → Aggregator will update active_profile (Phase 1C)
- `0.50–0.70` → low-confidence → `needs_clarification=True` → Agent 1 asks follow-up (Phase 1C)
- `< 0.50` → discarded

**Atomic writes:** fact_log.json uses write-to-.tmp → os.replace() pattern.
If process dies mid-write, original file is untouched. No corrupt JSON.

**low_confidence_fields — Phase 1C gap (known, intentional):**
Compressor writes `state.low_confidence_fields` but `state` is per-request and dies
after the background task. These ARE persisted in fact_log.json with
`needs_clarification=True`. The wire-up to Agent 1 (so it asks a clarification question
next turn) requires persistent storage — that's Phase 1C work.

### What each extracted fact contains

8 Compressor fields:
- `key` — snake_case name (weight, diet_type, chronic_illness, etc.)
- `value` — always a string, units normalized ("4 kilos" → value="4 kg", key="weight")
- `confidence` — 0.0–1.0 based on language certainty (vet-confirmed=0.95, hedged=0.60)
- `source_rank` — "vet_record" | "explicit_owner"
- `time_scope` — "current" | "past" | "unknown" — prevents overwriting past facts onto current profile
- `uncertainty` — plain text reason why confidence < 1.0, or ""
- `source_quote` — exact substring from user message supporting this fact
- `timestamp` — ISO datetime if user stated a time, else null

3 fields added by main.py when logging:
- `session_id` — which conversation produced this fact (traceable in fact_log.json)
- `extracted_at` — ISO UTC timestamp
- `needs_clarification` — true if confidence ≤ 0.70

### API changes

POST /chat now returns 3 extra fields visible in the response:
- `is_entity` — did Agent 1 detect extractable facts?
- `intent_type` — "health" | "food" | "general"
- `urgency` — "high" | "medium" | "low"

New debug endpoint: `GET /debug/facts?session_id=xxx` — reads fact_log.json filtered
by session. Gives the UI a way to see Agent 2 output after the background task finishes.

Browser console logs (in frontend/src/api.js):
- Agent 1 group logged immediately after each message (reply, intent, urgency, is_entity)
- Agent 2 group logged 8 seconds later (extracted facts with confidence + clarification flag)

### Files changed or created in Phase 1B

| File | What |
|---|---|
| `app/agents/conversation.py` | Agent 1 outputs `{reply, is_entity}` JSON. `_parse_agent_response()` added. `is_entity` in `AgentResponse`. |
| `app/agents/state.py` | NEW — AgentState dataclass |
| `app/storage/__init__.py` | NEW — empty package init |
| `app/storage/file_store.py` | NEW — `append_fact_log()`, `read_fact_log()`, atomic write |
| `app/agents/compressor.py` | NEW — Agent 2. `ExtractedFact` dataclass, `CompressorAgent`, confidence thresholds |
| `app/main.py` | AgentState built per request, `_compressor` global, `_run_background()`, debug fields in `ChatResponse`, `GET /debug/facts` |
| `.gitignore` | `data/` added |
| `tests/run_e2e.py` | NEW — 18 automated end-to-end tests, all passing |
| `frontend/src/api.js` | Agent 1 + Agent 2 console.log groups |
| `design-docs/compressor-design.md` | NEW — full design doc with decision log |

### Data Model Refactor (2026-03-08)

Replaced `dummy_context.py` with proper data structures and `context_builder.py`.
This was a prerequisite for the Aggregator — it needs structured storage to write into.

**What changed:**

1. **Three dataclasses** in `app/models/context.py`:
   - `PetProfile` — static pet identity (name, species, breed, dob, sex, life_stage)
   - `ActiveProfileEntry` — one dynamic fact (11 fields including status, change_detected, trend_flag)
   - `UserProfile` — owner relationship data (user_id, session_count, relationship_summary)

2. **JSON file storage** — three auto-seeded files in `data/`:
   - `pet_profile.json` — Luna defaults
   - `active_profile.json` — 5 dynamic entries + `_pet_history`
   - `user_profile.json` — Shara defaults

3. **`context_builder.py`** replaces `dummy_context.py`:
   - Reads JSON files every request (no caching — sub-millisecond)
   - Merges pet_profile static fields into active_profile as high-confidence entries
   - Computes `pet_summary` from template (no LLM) and `gap_list` from FULL_FIELD_LIST
   - Seeds defaults on first run — system works identically to before

4. **Key name alignment** — Compressor prompt updated to match `constants.py`:
   - `weight_kg` → `weight`, `current_medications` → `medications`
   - `neutered` → `neutered_spayed`, `vaccination_status` → `vaccinations`

5. **`constants.py`** expanded — 8 new FIELD_LABELS, added FULL_FIELD_LIST

6. **`dummy_context.py` deleted** — no remaining imports

7. **Design docs moved** to `design-docs/` folder

**All 18 e2e tests pass. JSON files auto-seed on first request.**

### Aggregator (Agent 3) — Complete ✓ (2026-03-08)

Pure deterministic logic — no LLM call. Reads facts from Compressor, applies conflict
resolution Rules 0–6, writes merged results into `active_profile.json`.

**Rules (priority order — first match wins):**
- Rule 0: time_scope gate — skip past/unknown facts (they go to fact_log only)
- Rule 1: First-time key — insert directly
- Rule 2: User correction (`source_rank="user_correction"`) — overwrite unconditionally
- Rule 3: Confirmation (same value) — boost confidence +0.05, status="confirmed"
- Rule 4: Low-confidence gate — skip if new confidence < current * 0.80
- Rule 5: Better fact (higher confidence or better source) — overwrite, status="updated"
- Rule 6: True conflict — keep current, log warning

**What changed:**
- `app/agents/aggregator.py` — NEW. `AggregatorAgent.run()` takes facts + session_id
- `app/agents/compressor.py` — Added `"user_correction"` to source_rank detection in prompt
- `app/routes/chat.py` — Aggregator call added after Compressor in `_run_background()`
- `app/routes/debug.py` — `GET /debug/profile` endpoint reads active_profile.json
- `frontend/src/api.js` — Agent 3 console.log group (amber, 8s delay)

**Confidence normalization:** Seed data uses integers (80), Compressor outputs floats (0.80).
Aggregator auto-normalizes: if `confidence > 1.0`, divides by 100.

6 new e2e tests added (24 total). All Aggregator tests pass.

### Route Refactor (2026-03-08)

Split monolithic `main.py` (~300 lines) into focused route modules using FastAPI `APIRouter`.

**Problem:** `main.py` held app creation, lifespan, CORS config, Pydantic models,
`POST /chat`, `_run_background()`, debug endpoints, and simulator endpoints.
Too many responsibilities for one file.

**Solution:** Extract routes into `app/routes/` with three modules:
- `chat.py` — `POST /chat`, request/response models, `_run_background()` pipeline
- `debug.py` — `GET /debug/facts`, `GET /debug/profile` (APIRouter with `/debug` prefix)
- `simulator.py` — `GET /health/chat`, `GET /food/chat` (Phase 1 HTML simulators)

**Shared state pattern:** Module globals (`_agent`, `_compressor`, etc.) replaced with
`app.state` set in lifespan. Routes access via `request.app.state`. Background pipeline
receives `state_bag` parameter referencing `app.state`.

**Result:** `main.py` reduced to ~120 lines (app creation, CORS, lifespan, `/health`).
Each route module is self-contained. 21/24 e2e tests pass (2 pre-existing LLM flakes,
1 transient 500 from hot-reload conflict during test run).

### Code Review + Quick Fixes (2026-03-08)

Full review of all backend files. Found 6 issues, fixed 5:

1. **Stale comment in `main.py`** — referenced `dummy_context.py` → updated to `context_builder.py`
2. **Stale comment in `main.py`** — Aggregator marked "(Phase 1C)" → removed, it was done
3. **Stale docstring in `deeplink.py`** — referenced `dummy_context.PET_SUMMARY` → fixed
4. **Fragile mutation in `context_builder.py`** — `.pop("_pet_history")` + re-insert
   replaced with safe `.get()` + `key.startswith("_")` filter (no dict mutation)
5. **Unused import cleanup** — minor import tidying

**Deferred:** Dead code in `constants.py` (`ENTITY_PATTERNS`, `MEDICAL_KEYWORDS`,
`NUTRITIONAL_KEYWORDS`) — left from Phase 1A regex removal. Cleanup tracked separately.

### Confidence Bar (2026-03-08)

User-facing score (0-100) showing how well AnyMall-chan knows the pet. Combines
three signals per field — all from existing data, no LLM calls:

**Formula:** `score = sum(field_confidence × decay × importance_weight) / 46 × 100`

**Three importance tiers (22 scored fields, `name` excluded):**
- Tier A (weight 3): species, breed, age, weight, diet_type, medications, chronic_illness, allergies
- Tier B (weight 2): sex, neutered_spayed, energy_level, appetite, vaccinations, past_conditions, food_brand
- Tier C (weight 1): temperament, behavioral_traits, activity_level, vet_name, last_vet_visit, microchipped, insurance, past_medications

Filling all Tier A + B = 82.6% → green. Tier C is bonus, not required.

**Four decay categories with exponential half-lives:**
- Static (never decays): species, breed, sex, neutered_spayed, microchipped
- Slow (180 days): allergies, chronic_illness, temperament, behavioral_traits, insurance
- Medium (90 days): diet_type, food_brand, medications, vaccinations, vet_name, last_vet_visit
- Fast (45 days): weight, age, energy_level, appetite, activity_level, past_conditions, past_medications

**Life stage multiplier (divides half-life → faster decay for young/old pets):**
- Puppy/kitten: fast×2, medium×1.5, slow×1.25
- Junior: fast×1.5, medium×1.25
- Adult: standard (all ×1)
- Senior: fast×1.5, medium×1.25

Decay floored at 0.3 — old data is still better than nothing.

**Key design decisions:**
- Dropped PRD's "depth" component — Compressor confidence already captures quality
- Computed on-the-fly (not persisted) — decay depends on current time
- All config lives in `confidence_calculator.py` (only consumer)

**Files:**
- `app/services/confidence_calculator.py` — pure functions, all config + calculation
- `app/routes/chat.py` — ChatResponse includes `confidence_score` (int) and `confidence_color`
- `design-docs/confidence-bar.md` — full design doc with decision log

**Frontend wiring:** Connected existing `ConfidenceBar` component (compact variant) to
the `/chat` response in `frontend/src/screens/Chat.jsx`. Shows colored progress bar + percentage
in the chat header next to the pet name. Updates after every message.

### Prompt v2 — PRD-Aligned System Prompt (2026-03-09)

Rewrote Agent 1's system prompt to align with PW1-PRD v0.2b (25-page document from
prompt engineering team). The PRD defines AnyMall-chan's persona, tone, emoji rules,
speech quirks, question discipline, and redirect protocol.

**Process:**
1. Extracted PDF contents (PyMuPDF) and analyzed 14 prompt sections
2. Wrote gap analysis (`design-docs/prompt-gap-analysis.md`) — 17 gaps identified
3. Wrote proposed prompt (`design-docs/prompt-v2-proposal.md`) — reviewed with user
4. User made decisions on all 17 gaps, then approved implementation

**What changed:**

1. **`constants.py`** — `MAX_QUESTIONS_PER_SESSION` reduced from 5 to 3 (PRD guidance).
   Replaced flat `GAP_QUESTION_HINTS` dict (13 EN-only fields) with `GAP_PRIORITY_LADDER`
   (Rank A-D, bilingual hints: `hint_en` + `hint_ja`). `HIGH_PRIORITY_FIELDS` reordered
   to match PRD Rank A (chronic_illness, allergies first).

2. **`app/agents/conversation.py`** — Complete prompt rewrite:
   - Identity block: "You are AnyMall-chan" with persona rules
   - Pet suffix rules: -chan/ちゃん for female, -kun/くん for male (from pet sex + language)
   - Emoji budget: max 2 per reply (0 for urgent health)
   - Speech quirks: sentence-final particles in JA mode
   - Response policy: 4-step decision flow (empathize → answer → ask → close)
   - Priority Ladder gap questions: walks Rank A→D, picks FIRST missing field
   - Soft redirect for health/food: "learning tool" framing, not "expert consultation"
   - Emergency override: urgency=high → direct vet-contact advice, 0 emojis
   - Prompt injection defense section
   - `run()` now accepts `urgency` and `language_str` params
   - `_build_gap_section()` rewritten for bilingual priority ladder
   - `_build_flag_section()` has 3 modes: URGENT, HEALTH (soft), FOOD (soft)
   - New `_build_pet_suffix()` static method

3. **`app/routes/chat.py`** — Passes `urgency` and `language_str="EN"` to `agent.run()`.
   Question counting now includes Japanese "？" marks.

**Design docs created:**
- `design-docs/prompt-gap-analysis.md` — 17 gaps with severity ratings, side-by-side comparison
- `design-docs/prompt-v2-proposal.md` — full proposed prompt text with review checklist

**Test results:** 22/24 e2e tests pass. 2 failures are pre-existing Compressor LLM flakes
(multi-fact extraction), unrelated to prompt changes.

### Reviewer Feedback v1 — 6 Issues Fixed (2026-03-12)

Product team reviewed the build and sent `reviewer-feedback-v1-proposal.md` with 7 issues.
We fixed 6 of them (Issue 6 was N/A — already handled by prompt v2).

**Issue 2 — Response structure:** Rewrote the response policy section in Agent 1's prompt.
Now says "End with exactly 1 gentle follow-up question" instead of leaving it ambiguous.

**Issue 3 — Pet name as subject:** Added HARD RULE #5 to Agent 1: "Always use the pet's
name as the explicit subject in examples and advice." Before: "Make sure to give plenty of
water." After: "Make sure Luna-chan gets plenty of water!"

**Issue 4 — asked_gap_question tracking:** Added `asked_gap_question: bool` to Agent 1's
JSON output format. Replaces the old `?` counting heuristic (which broke on Japanese `？`
and rhetorical questions). Agent 1 now explicitly signals whether it asked a gap question.

**Issue 5 — Language detection:** Added `_detect_language()` in `chat.py` using Unicode
range checks (Hiragana U+3040-309F, Katakana U+30A0-30FF, CJK U+4E00-9FFF). If >30% of
non-ASCII chars are Japanese, returns "JA", else "EN". Replaces hardcoded `language_str="EN"`.

**Issue 7 — Emoji discipline:** Changed emoji rule from "max 2 per reply" to "max 2 per
reply, first mention only". Prevents emoji repetition within a single response.

**Food urgency gating:** IntentClassifier now returns real urgency for food intents (was
always "low"). `build_deeplink()` passes urgency through. In `chat.py`, LOW urgency food
intents return no redirect. Medium urgency food gated by cooldown (once per 3 messages).

**Files changed:** `conversation.py`, `intent_classifier.py`, `chat.py`, `deeplink.py`, `run_e2e.py`

### In-Memory Profile Optimization (2026-03-12)

Eliminated per-request disk I/O on the hot path. Before: every `/chat` request read
`active_profile.json`, `pet_profile.json`, `user_profile.json` from disk. After: profiles
loaded once at startup into `app.state`, all runtime reads come from memory.

**How it works:**
1. `load_profiles()` in `context_builder.py` — called once during `lifespan()` startup
2. Returns `{"active": dict, "pet": dict, "user": dict}` — stored on `app.state`
3. `build_context()` parameterized — accepts optional in-memory profiles
4. When params are `None` → falls back to disk read (backward compatible)
5. Aggregator receives `app.state.active_profile` by reference — mutations visible immediately
6. After mutation, Aggregator writes through to disk for persistence (survives restarts)

**Concurrency:** FastAPI single-threaded asyncio. Dict reads in `/chat` are synchronous
(no `await`), always consistent. Aggregator's `asyncio.Lock` prevents concurrent mutations.

**GET /confidence endpoint:** Dedicated `GET /confidence` in `main.py` so frontend can
fetch score on mount without waiting for `/chat`. Reads from `app.state` (in-memory).
Frontend calls `fetchConfidence()` on mount and 4s after each message.

**Sticky redirect nudge:** Frontend changed from inline per-message redirect buttons to
a single persistent nudge bar above the input field. Cleaner UX, doesn't clutter chat history.

**Files changed:** `context_builder.py`, `main.py`, `chat.py`, `aggregator.py`, `api.js`, `Chat.jsx`, `Chat.css`

### What is pending after Phase 1B

- **Clarification loop** — low_confidence_fields feed back to Agent 1 next turn (Phase 1C).
- **Dead code cleanup** — remove unused regex constants from constants.py.
- **4-second confidence delay** — three options identified: (A) cache score in _run_background, (B) SSE push, (C) accept delay. Tracked in progress.json future_tasks.

---

## Running the project

```bash
# Terminal 1 — backend
cd backend
source .venv/Scripts/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — React test UI
cd frontend
npm run dev
```

Open `http://localhost:5173` in the browser. Type a message. Watch the terminal for logs.

Every chat request now logs:
```
IntentClassifier: intent=health urgency=high confidence=9 (attempt 1)
Agent1.run — pet=Luna  gaps=5  questions_so_far=0
Agent1 reply — length=120 chars  questions_this_turn=0
Chat complete — session=... | intent=health | urgency=high | questions=0 | guardrailed=False
```
