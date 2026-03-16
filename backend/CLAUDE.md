# AnyMall-chan Backend — Claude Project Context

## What This Project Is

AnyMall-chan is a **pet companion chat application**.

**Project layout (`AnyMall-chat/`):**
```
AnyMall-chat/
├── backend/     <- Python + FastAPI — THIS is what we build (git initialized here)
└── frontend/    <- React + Vite — testing UI only, used to visually test the backend API
```

**Two separate frontends — do not confuse them:**
- `frontend/` (React) — exists only to test backend responses visually during development.
  We use this to see real chat UI while building. It is NOT the production app.
  **You MAY edit React frontend files** when the user asks — e.g. adding console.log,
  debug panels, or wiring new API fields into the UI. Keep changes minimal and testing-only.
- Flutter iOS app — the real production mobile app, built by a separate team.
  We do not touch it. It will consume the same API when ready.

**Our primary job:** Build the backend. The React frontend is a testing tool we can edit.
**Never write Flutter code** — that is the separate team's responsibility.
The React frontend already exists and is used as-is for testing.

---

## Build Philosophy: Simple First, Complex Later

We build the minimum that works at each phase. We do not add complexity until the
simple version is working and understood. Every line of code is written with full
understanding of what it does and why it is there.

**Current goal: Phase 2 ✓ complete, API v1 ✓ complete → Phase 3 (nightly batch jobs) next.**

Phase 0 ✓. Phase 1A ✓. Phase 1B ✓. Phase 1C ✓ (PostgreSQL replaces JSON files).
Phase 2 ✓ (Thread & Conversation Management — 24h thread windows, write-through message persistence, startup reload, LLM compaction, cross-thread continuity via conversation_summary).
API v1 ✓ (versioned endpoints under `/api/v1/`, standardized error contract, restructured redirect payload, `pet_context` removed from request).

**Known gaps before production (tracked in progress.json future_tasks):**
- `ft-002`: Cleanup — delete deprecated `file_store.py`, `app/models/context.py`, `data/` directory
- `ft-003`: Wire `x-user-code` header — blocked on Flutter team clarification. Currently `DEFAULT_PET_ID="luna-001"` is hardcoded.
- `ft-011`: AALDA API integration — backend should fetch pet data by `pet_id` instead of using defaults. Blocked on API details from Flutter team. See `design-docs/aalda-integration.md`.
- `ft-012`: Remove DEFAULT_PET_ID/USER_ID fallbacks — after ft-011 and ft-003 are done.

---

## Phase 0 — Completed ✓

All 12 features written and manually tested. Chat endpoint works end-to-end.
See `notes.md` for a plain-language summary of what was built and why.
See `design-docs/security.md` for all known production risks and their fix phases.

---

## Phase 1A — Completed ✓

IntentClassifier added. Redirect/deeplink logic wired. Regex entity pipeline removed.
See `notes.md` Phase 1A section for full details.

**What Phase 1A added:**
- `app/agents/intent_classifier.py` — LLM-based classifier runs before Agent 1 every request
- `app/services/deeplink.py` — builds redirect payload for health/food intents
- Simulator endpoints: `GET /api/v1/simulator/health` and `GET /api/v1/simulator/food`

**What Phase 1A removed:**
- `IntentFlags` dataclass and `classify_intent()` from `guardrails.py` — replaced by LLM
- Dead keyword lists from `constants.py` — LLM handles this now

---

## Phase 1B — Complete ✓

**All agents built and tested. Routes refactored. Confidence calculator added. Prompt v2 (PRD-aligned) deployed.**

**Current pipeline (Phase 2 complete):**
```
User message
    → Thread boundary logic       resolve session_id → thread_id (DB lookup, 24h expiry check)
    → IntentClassifier (LLM)      health / food / general + urgency
    → _detect_language()          Unicode range check → "EN" or "JA"
    → Agent 1 (LLM)               outputs {"reply": "...", "is_entity": bool, "asked_gap_question": bool}
                                   receives conversation_summary from thread compaction
    → apply_guardrails()
    → build_deeplink()            (food LOW urgency → no redirect)
    → confidence_calculator()     confidence_score + confidence_color (reads from app.state)
    → Append to app.state.sessions[thread_id]  (in-memory, keyed by thread_id)
    → Return response to user     (includes status, thread_id, new_thread, is_entity, intent_type, urgency, confidence)
    ↓  [fire-and-forget — user does NOT wait]
    → _run_background(AgentState)
         → Write-through messages   → PostgreSQL thread_messages table
         → Compaction check         → if >= 50 messages, fire _run_compaction() task
         → Compressor (LLM, temp=0.0)   → PostgreSQL fact_log table
         → Aggregator (no LLM)          → app.state.active_profile (mutates in place) + write-through to PostgreSQL
```

**In-memory patterns:**
- `load_profiles_from_db()` in `context_builder.py` called once at startup → loads profiles into `app.state`
- Active threads reloaded from PostgreSQL at startup → `app.state.sessions` (keyed by `thread_id`)
- All runtime reads from `app.state` (no disk I/O or DB I/O on hot path)
- Aggregator mutates `app.state.active_profile` by reference, writes through to PostgreSQL for persistence
- Messages appended to `app.state.sessions[thread_id]` synchronously, written through to `thread_messages` table in `_run_background()`
- `build_context()` accepts optional in-memory profiles + `conversation_summary`; returns 6 values
- `GET /api/v1/confidence` reads from `app.state` — frontend calls on mount + 4s after each message

**File structure — current state (Phase 2 complete):**
```
backend/
|-- app/
|   |-- agents/
|   |   |-- conversation.py          # Agent 1 — PRD-aligned bilingual prompt, outputs {reply, is_entity} JSON
|   |   |-- intent_classifier.py     # IntentClassifier — Phase 1A
|   |   |-- state.py                 # AgentState dataclass (includes thread_id)
|   |   |-- compressor.py            # Agent 2 — fact extraction (LLM, temp=0.0)
|   |   `-- aggregator.py            # Agent 3 — fact merge (no LLM, Rules 0-6), write-through to PostgreSQL
|   |-- db/                          # PostgreSQL layer
|   |   |-- __init__.py
|   |   |-- session.py               # init_db(), dispose_engine(), get_session() async context manager
|   |   |-- models.py                # SQLAlchemy 2.0 ORM: Pet, User, ActiveProfile, FactLog, Thread, ThreadMessage
|   |   `-- repositories.py          # PetRepo, UserRepo, ActiveProfileRepo, FactLogRepo, ThreadRepo, ThreadMessageRepo
|   |-- routes/
|   |   |-- __init__.py
|   |   |-- chat.py                  # POST /api/v1/chat + GET /api/v1/confidence + thread boundary + _run_background()
|   |   |-- debug.py                 # GET /api/v1/debug/facts, /profile, /threads, /thread/{id}/messages
|   |   `-- simulator.py             # GET /api/v1/simulator/health, GET /api/v1/simulator/food
|   |-- services/
|   |   |-- guardrails.py            # apply_guardrails() only
|   |   |-- deeplink.py              # build_deeplink() — returns data (module, display, context), no URLs
|   |   |-- context_builder.py       # load_profiles_from_db() + build_context() — returns 6 context values
|   |   |-- confidence_calculator.py # confidence_score + confidence_color
|   |   `-- thread_summarizer.py     # Phase 2 — LLM summarization for thread compaction
|   |-- storage/
|   |   |-- __init__.py
|   |   `-- file_store.py            # DEPRECATED — replaced by repositories.py. Scheduled for deletion (ft-002).
|   |-- models/
|   |   |-- __init__.py
|   |   `-- context.py               # DEPRECATED — replaced by app/db/models.py. Scheduled for deletion (ft-002).
|   |-- llm/
|   |   |-- base.py                  # Abstract LLMProvider
|   |   |-- azure_openai.py          # Azure implementation
|   |   `-- factory.py               # creates provider from settings
|   `-- core/
|       `-- config.py                # reads .env -> Settings (includes database_url)
|-- constants.py                     # business logic constants + FULL_FIELD_LIST + GAP_PRIORITY_LADDER + thread constants
|-- docker-compose.yml               # PostgreSQL 16 Alpine container (port 5433:5432)
|-- alembic.ini                      # Alembic migration config
|-- migrations/                      # Alembic migration scripts
|   |-- env.py                       # async runner, imports Base.metadata from app.db.models
|   `-- versions/                    # migration files (3 total: initial + indexes + threads)
|-- design-docs/                     # all design & architecture documents
|   |-- aggregator-design.md         # Aggregator design doc
|   |-- compressor-design.md         # Compressor design doc + decision log
|   |-- confidence-bar.md            # Confidence bar formula, tiers, decay, decision log
|   |-- security.md                  # production security risks + fix phases
|   |-- system-design.md             # full system architecture
|   |-- system.md                    # PRD review notes
|   |-- session-management.md        # Phase 2 design doc — thread lifecycle, compaction, write-through
|   |-- prompt-gap-analysis.md       # 17-gap comparison: current prompt vs PW1-PRD v0.2b
|   |-- prompt-v2-proposal.md        # Approved prompt v2 design + review checklist
|   `-- api-v1-design.md             # API v1 design doc — versioning, error contract, redirect structure
|-- app/main.py                      # FastAPI app creation, CORS, lifespan, /health, error handlers, DB init
|-- tests/
|   `-- run_e2e.py                   # e2e tests (8 sections: infra, intent, session, compressor, aggregator, DB, threads, API v1)
`-- data/                            # gitignored — DEPRECATED (Phase 1B legacy, no longer written to)
```

---

## Pet Context (context_builder.py)

`build_context()` accepts in-memory dicts from `app.state` and returns 6 values every request:

```python
active_profile: dict   # structured facts with confidence scores and source
gap_list: list[str]    # field names we don't know yet (weight, allergies, etc.)
pet_summary: str       # "Luna is a 1 year-old female Shiba Inu..." (computed, not stored)
pet_history: str       # "3 weeks ago: ear infection. Antibiotics prescribed..."
relationship_context: str  # "Owner (Shara) tends to be anxious. Prefers short replies..."
conversation_summary: str  # Phase 2: compaction summary from thread (pass-through, "" if none)
```

On first run, `load_profiles_from_db()` seeds Luna + Shara defaults to PostgreSQL. Agent 1 never knows the source.

---

## Testing the Backend

**Option 1 — React UI (recommended for visual testing):**
```bash
# Terminal 1 — backend
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — React test UI
cd frontend
npm run dev   (starts on http://localhost:5173)
```

**Option 2 — curl:**
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Luna seems tired today", "session_id": "test-1"}'
```

**Tip: if the server starts but you see no logs and get old responses**, another process
is holding port 8000. Run `netstat -ano | findstr :8000` to find it. Kill the PID or reboot.

CORS is configured with `allow_origins=["*"]` during development.
Lock it down to specific origins before production.

---

## Three Architecture Patterns (Apply From Day 1)

### 1. LLM Provider — Strategy Pattern
One abstract `LLMProvider` class. All agents receive an instance of it via constructor.
Never import a concrete provider inside an agent. Just call `self._llm.complete()`.
To swap providers: change one env var (`LLM_PROVIDER`). Zero agent code changes.

### 2. Services are pure functions
`guardrails.py` takes a string in, returns a result. No global state.
`conversation.py` takes context in, calls LLM, returns result.
No side effects. No imports of global state.

### 3. Config comes from environment
Never hardcode API keys or endpoints. Always from `.env` via pydantic-settings.
Test data (Luna + Shara defaults) is seeded via `context_builder.py` into PostgreSQL on first startup.

---

## LLM Configuration

Provider: **Azure OpenAI** (current)
- IntentClassifier uses: `temperature=0.0`, `max_tokens=48` — deterministic, tiny output
- Agent 1 uses: `temperature=0.7`, `max_tokens=512` — conversational
- Agent 2 (Compressor) uses: `temperature=0.0`, `max_tokens=400` — deterministic extraction
- Agent 3 (Aggregator) uses: no LLM — pure deterministic rules (Rules 0-6)
- ThreadSummarizer uses: `temperature=0.0`, `max_tokens=400` — deterministic compaction

Deployment name: `gpt-4.1` (configured via `AZURE_OPENAI_DEPLOYMENT_CHAT` in `.env`).
Migration path: set `LLM_PROVIDER=openai` in `.env`. No agent code changes.

---

## Security Rules

- No secrets in code. Ever.
- API keys, endpoints -> `.env` only
- `.env` is gitignored. `.env.example` has placeholder values only.
- All secrets loaded through `app/core/config.py`

---

## How We Work Together (Claude Behaviour Rules)

- **Explain before writing.** Before writing any file, explain what it does,
  why it exists, and what every major section contains. Wait for the user to
  say "write it" (or similar) before generating code.
- **One file at a time.** Never write multiple files in one response.
  Write one file, explain it, wait for confirmation, then move to the next.
- **No surprises.** If a design decision needs to be made, surface it and
  discuss it before writing code that encodes that decision.

---

## Code Quality Rules (Apply From Day 1)

- Every route: `async def` — we make external LLM calls
- Type hints on every function signature
- `logger = logging.getLogger(__name__)` in every module, never `print()`
- All imports at the top of the file — no imports inside functions
- One responsibility per file

---

## What Each Phase Adds

```
Phase 0  (DONE): POST /chat → Agent 1 → response. Hardcoded pet context.
                 (Note: /chat is now /api/v1/chat after API v1 migration)

Phase 1A (DONE): IntentClassifier (LLM) before Agent 1. Health/food redirect logic.
                 Removed regex entity pipeline. Deeplink payload in API response.

Phase 1B (DONE): Agent 2 (Compressor) ✓ — extracts facts → fact_log.
                  Agent 3 (Aggregator) ✓ — merges facts → active_profile.
                  Data model + context_builder.py ✓. Route refactor ✓.
                  Confidence calculator ✓. Prompt v2 ✓. Reviewer feedback v1 ✓.
                  In-memory profile optimization ✓. GET /confidence endpoint ✓.

Phase 1C (DONE): PostgreSQL replaces JSON files. Docker Compose + SQLAlchemy 2.0 async +
                  Alembic migrations + repository pattern. Zero agent logic changes.
                  file_store.py deprecated. All reads/writes go through app/db/ layer.

Phase 2  (DONE): Thread & Conversation Management. 24h thread windows with hard expiry.
                  Write-through message persistence to PostgreSQL. Startup reload of
                  active threads. LLM compaction (ThreadSummarizer) when messages exceed 50.
                  Cross-thread continuity via conversation_summary passed to Agent 1.
                  New tables: threads, thread_messages. New debug endpoints.

API v1  (DONE):  All endpoints versioned under /api/v1/ (except /health).
                  Standardized error contract: {"status":"error","error":{"code","message"}}.
                  Redirect payload restructured: display{label,style} + context{query,pet_id,pet_summary}.
                  pet_context removed from request — backend uses pet_id + DEFAULT_PET_ID fallback.
                  See design-docs/api-v1-design.md for full specification.

Phase 3:         Nightly batch jobs

Phase 4:         JWT auth + rate limiting

Phase 5:         Tests + production deployment
```

---

## Database Tables (Live in PostgreSQL)

| Table | Write Pattern | Purpose | ORM Model |
|---|---|---|---|
| `pets` | UPSERT | Pet identity: name, species, breed | `app.db.models.Pet` |
| `users` | UPSERT | Owner relationship data | `app.db.models.User` |
| `fact_log` | APPEND only | Every extracted fact, full audit trail | `app.db.models.FactLog` |
| `active_profile` | DELETE+INSERT (per pet) | Current best-known value per field | `app.db.models.ActiveProfile` |
| `threads` | INSERT + UPDATE status/summary | 24h conversation windows | `app.db.models.Thread` |
| `thread_messages` | APPEND only | Individual messages within threads | `app.db.models.ThreadMessage` |

**Note:** `_pet_history` is stored as a row in `active_profile` with `field_key="_pet_history"` and NULL metadata columns.

---

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env — fill in your Azure OpenAI credentials + DATABASE_URL

# 3. Start PostgreSQL (Docker required)
docker compose up -d
# Verify: docker exec -it anymall-postgres psql -U anymall -d anymallchan -c "\dt"

# 4. Run database migrations
alembic upgrade head

# 5. Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Test
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Luna seems tired today", "session_id": "test-1"}'
```

**Note:** PostgreSQL runs on port 5433 (not 5432) to avoid conflicts with any native PostgreSQL installation. The `DATABASE_URL` in `.env` already points to port 5433.
