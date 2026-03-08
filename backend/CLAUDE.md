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

**Current goal: Phase 1B ✓ complete → Phase 1C (PostgreSQL) next.**

Phase 0 ✓. Phase 1A ✓. Phase 1B ✓ (Compressor + Aggregator + route refactor + confidence calculator).
24 e2e tests (21 passing, 2 pre-existing LLM flakes, 1 transient).
Next: Phase 1C — swap JSON files for PostgreSQL.

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
- Simulator endpoints: `GET /health/chat` and `GET /food/chat`

**What Phase 1A removed:**
- `IntentFlags` dataclass and `classify_intent()` from `guardrails.py` — replaced by LLM
- Dead keyword lists from `constants.py` — LLM handles this now

---

## Phase 1B — Complete ✓

**All agents built and tested. Routes refactored. Confidence calculator added.**

**Current pipeline (Phase 1B complete):**
```
User message
    → IntentClassifier (LLM)      health / food / general + urgency
    → Agent 1 (LLM)               outputs {"reply": "...", "is_entity": bool}
    → apply_guardrails()
    → build_deeplink()
    → confidence_calculator()     confidence_score + confidence_color
    → Return response to user     (includes is_entity, intent_type, urgency, confidence)
    ↓  [fire-and-forget — user does NOT wait]
    → _run_background(AgentState)
         → Compressor (LLM, temp=0.0)   → fact_log.json
         → Aggregator (no LLM)          → active_profile.json
```

**File structure — current state (Phase 1B complete):**
```
backend/
|-- app/
|   |-- agents/
|   |   |-- conversation.py          # Agent 1 — outputs {reply, is_entity} JSON
|   |   |-- intent_classifier.py     # IntentClassifier — Phase 1A
|   |   |-- state.py                 # AgentState dataclass
|   |   |-- compressor.py            # Agent 2 — fact extraction (LLM, temp=0.0)
|   |   `-- aggregator.py            # Agent 3 — fact merge (no LLM, Rules 0-6)
|   |-- routes/
|   |   |-- __init__.py
|   |   |-- chat.py                  # POST /chat + _run_background() + Pydantic models
|   |   |-- debug.py                 # GET /debug/facts, GET /debug/profile
|   |   `-- simulator.py             # GET /health/chat, GET /food/chat (Phase 1 HTML)
|   |-- services/
|   |   |-- guardrails.py            # apply_guardrails() only
|   |   |-- deeplink.py              # build_deeplink()
|   |   |-- context_builder.py       # reads JSON files, returns 5 context values
|   |   `-- confidence_calculator.py # confidence_score + confidence_color
|   |-- storage/
|   |   |-- __init__.py
|   |   `-- file_store.py            # fact_log + pet/active/user profile read/write
|   |-- models/
|   |   |-- __init__.py
|   |   `-- context.py               # PetProfile, ActiveProfileEntry, UserProfile
|   |-- llm/
|   |   |-- base.py                  # Abstract LLMProvider
|   |   |-- azure_openai.py          # Azure implementation
|   |   `-- factory.py               # creates provider from settings
|   `-- core/
|       `-- config.py                # reads .env -> Settings
|-- constants.py                     # business logic constants + FULL_FIELD_LIST
|-- design-docs/                     # all design & architecture documents
|   |-- aggregator-design.md         # Aggregator design doc
|   |-- compressor-design.md         # Compressor design doc + decision log
|   |-- confidence-bar.md            # Confidence bar formula, tiers, decay, decision log
|   |-- security.md                  # production security risks + fix phases
|   |-- system-design.md             # full system architecture
|   `-- system.md                    # PRD review notes
|-- app/main.py                      # FastAPI app creation, CORS, lifespan, /health
|-- tests/
|   `-- run_e2e.py                   # 24 automated end-to-end tests
`-- data/                            # gitignored — created at runtime
    |-- fact_log.json                # append-only extracted facts log
    |-- pet_profile.json             # static pet identity (auto-seeded)
    |-- active_profile.json          # dynamic facts per field (Aggregator writes here)
    `-- user_profile.json            # owner relationship data (auto-seeded)
```

**Phase 1C (after Aggregator):** Swap JSON files for PostgreSQL.
`context_builder.py` and `file_store.py` get PostgreSQL calls — agent code does not change.

---

## Pet Context (context_builder.py)

`context_builder.py` reads JSON files from `data/` and returns 5 values every request:

```python
active_profile: dict   # structured facts with confidence scores and source
gap_list: list[str]    # field names we don't know yet (weight, allergies, etc.)
pet_summary: str       # "Luna is a 1 year-old female Shiba Inu..." (computed, not stored)
pet_history: str       # "3 weeks ago: ear infection. Antibiotics prescribed..."
relationship_context: str  # "Owner (Shara) tends to be anxious. Prefers short replies..."
```

On first run, seeds Luna + Shara defaults to JSON files. Agent 1 never knows the source.

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
curl -X POST http://localhost:8000/chat \
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
Test data (Luna + Shara defaults) is seeded via `context_builder.py` into JSON files.

---

## LLM Configuration

Provider: **Azure OpenAI** (current)
- IntentClassifier uses: `temperature=0.0`, `max_tokens=48` — deterministic, tiny output
- Agent 1 uses: `temperature=0.7`, `max_tokens=512` — conversational
- Agent 2 (Compressor) uses: `temperature=0.0`, `max_tokens=400` — deterministic extraction
- Agent 3 (Aggregator) uses: no LLM — pure deterministic rules (Rules 0-6)

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

Phase 1A (DONE): IntentClassifier (LLM) before Agent 1. Health/food redirect logic.
                 Removed regex entity pipeline. Deeplink payload in API response.

Phase 1B (DONE): Agent 2 (Compressor) ✓ — extracts facts → fact_log.json.
                  Agent 3 (Aggregator) ✓ — merges facts → active_profile.json.
                  Data model + context_builder.py ✓. Route refactor ✓.
                  Confidence calculator ✓. 24 e2e tests.

Phase 1C:        Swap JSON files for real PostgreSQL.
                 context_builder.py + file_store.py get PostgreSQL calls.

Phase 2:         Add Redis cache (active_profile cache-aside)

Phase 3:         Session compaction, nightly batch jobs

Phase 4:         JWT auth + rate limiting

Phase 5:         Tests + production deployment
```

---

## Database Tables (For Reference — Phase 1C and Later)

| Table | Write Pattern | Purpose |
|---|---|---|
| `pets` | INSERT once | Pet identity: name, species, breed |
| `fact_log` | APPEND only | Every extracted fact, full audit trail |
| `active_profile` | UPSERT (one row per pet+key) | Current best-known value per field |
| `conversation_log` | APPEND only | Every chat message |
| `compressed_history` | APPEND (new row per compaction) | NL summaries of past sessions |

During Phase 1B, these same structures exist as JSON files:
- `data/fact_log.json` — append-only list
- `data/active_profile.json` — dict keyed by field name

---

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env — fill in your Azure OpenAI credentials

# 3. Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Test
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Luna seems tired today", "session_id": "test-1"}'
```
