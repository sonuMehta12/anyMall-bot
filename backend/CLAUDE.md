# AnyMall-chan Backend

## What This Is

AnyMall-chan is a **pet companion chat application**. Owners chat with an AI assistant about their pets (food, health, behavior, daily care). The system learns about each pet over time and becomes more contextual.

**Project layout:**
```
AnyMall-chat/
  backend/     <- Python + FastAPI (this is what we build)
  frontend/    <- React + Vite (testing UI only, not the production app)
```

**Two frontends exist:**
- `frontend/` (React) — dev testing UI. We can edit it for testing/debugging.
- Flutter iOS app — production mobile app, built by a separate team. We never touch it.

**Our job: build the backend.** The Flutter team consumes our API.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI (async) |
| LLM | OpenAI API (gpt-5.4 default) via `openai` Python SDK |
| Database | PostgreSQL 16 (source of truth) via SQLAlchemy 2.0 async + asyncpg |
| Cache | Valkey 8 (Redis-compatible hot cache) via valkey-py async |
| Migrations | Alembic |
| Scheduler | APScheduler (nightly batch jobs) |
| Pet data | AALDA external API (sole source of truth for pet profiles) |
| Auth | X-User-Code header (JWT planned for Phase 4) |
| Frontend (dev) | React + Vite |
| Containers | Docker Compose (PostgreSQL + Valkey) |

---

## Architecture Patterns

### 1. LLM Provider — Strategy Pattern
One abstract `LLMProvider` class (`app/llm/base.py`). All agents receive an instance via constructor. They call `self._llm.complete()` — never import a concrete provider. To swap providers: change `LLM_PROVIDER` in `.env`. Zero agent code changes. The provider handles model-specific quirks (e.g. `reasoning_effort="none"` for gpt-5.x) transparently.

### 2. Per-Agent Model Override
Each agent has a `_MODEL: str | None = None` constant near the top. `None` = use the provider default from `.env`. Set it to a specific model name (e.g. `"gpt-5.4-nano"`) to override for that agent only. See `design-docs/model-strategy.md` for the two-tier strategy (Chat tier vs Fast tier).

### 3. Repository Pattern
All database access goes through repository classes (`app/db/repositories.py`). Routes and agents never write raw SQL or touch SQLAlchemy models directly. To swap storage: only change the repository file.

### 4. Cache-Aside with Write-Through
PostgreSQL is the source of truth. All writes go to DB first, then Valkey. Cache misses fall back to DB and populate Valkey. Valkey is a soft dependency — the app degrades gracefully when Valkey is down (circuit breaker in `ValkeyClient`).

### 5. Services are Pure Functions
`guardrails.py`, `confidence_calculator.py`, `context_builder.py` — take input, return output. No global state, no side effects, no LLM calls.

### 6. Config from Environment
Never hardcode API keys or endpoints. Everything from `.env` via pydantic-settings (`app/core/config.py`).

---

## Request Pipeline

```
User message + X-User-Code header + pet_ids[]
  -> Auth check (401 if missing)
  -> AALDA fetch (parallel for 2 pets, Valkey cached)
  -> Thread boundary (resolve session -> 24h thread window)
  -> IntentClassifier (LLM: health/food/general + urgency)
  -> ConversationAgent (LLM: generates reply)
  -> Guardrails (regex filter)
  -> Deeplink (redirect payload if health/food intent)
  -> Confidence score (pure arithmetic)
  -> Return response to user
  |
  v  [fire-and-forget, user does NOT wait]
  -> Compressor (LLM: extract facts)
  -> Aggregator (no LLM: merge facts into active_profile, bust suggested-questions cache)
  -> Clarification management (low-confidence facts)
```

---

## Storage Patterns

| Key pattern | TTL | What it stores |
|-------------|-----|---------------|
| `am:session:{thread_id}` | 7200s | Message list for the thread |
| `am:profile:{pet_id}` | 3600s | Active profile (known facts) |
| `am:meta:{thread_id}` | 7200s | Gap question counter, redirect cooldown |
| `am:user:{user_code}` | 7200s | User record (language, relationship_summary) |
| `am:aalda:{user_code}:{pet_id}` | 300s | AALDA API response cache |
| `am:suggested:{user_code}:{pet_ids}:{lang}` | 10 days | Pre-generated suggested questions |
| `am:suggested_history:{user_code}:{pet_ids}` | 30 days | 4-week question history (anti-repeat) |

All keys use `jittered_ttl()` to prevent stampede expiration.

---

## Database Tables

| Table | Write Pattern | Purpose |
|-------|--------------|---------|
| `anymall_chan_users` | UPSERT per chat | Owner data, relationship_summary |
| `anymall_chan_fact_log` | APPEND only | Every extracted fact (audit trail) |
| `anymall_chan_active_profile` | DELETE+INSERT per pet | Current best-known value per field |
| `anymall_chan_threads` | INSERT + UPDATE | 24h conversation windows |
| `anymall_chan_thread_messages` | APPEND only | Individual messages within threads |

---

## File Structure

```
backend/app/
  core/
    config.py               # .env -> typed Settings object
  llm/
    base.py                 # Abstract LLMProvider + LLMProviderError
    openai_provider.py      # OpenAI implementation (handles gpt-5.x reasoning models)
    azure_openai.py         # Azure implementation
    factory.py              # Creates provider from .env settings
  agents/
    state.py                # AgentState + PetInfo dataclasses
    conversation.py         # Agent 1: main chat (temp=0.7)
    intent_classifier.py    # Intent + urgency classification (temp=0.0)
    compressor.py           # Agent 2: fact extraction (temp=0.0)
    aggregator.py           # Agent 3: fact merging (no LLM, Rules 0-6)
    suggested_questions.py  # Home screen question generator (temp=0.9)
  services/
    pet_fetcher.py          # AALDA API client with Valkey cache
    context_builder.py      # Merges AALDA + active_profile, computes gap_list
    confidence_calculator.py # Pure arithmetic scoring (0-100)
    guardrails.py           # Regex-based reply filtering
    deeplink.py             # Redirect payload builder
    question_templates.py   # Evergreen fallback questions (8 languages)
    question_validator.py   # Validates suggested questions
    thread_summarizer.py    # LLM thread compaction
    history_builder.py      # fact_log -> _pet_history narrative
    relationship_builder.py # USER STYLE -> relationship_summary
  db/
    models.py               # SQLAlchemy ORM models
    session.py              # Async session factory
    repositories.py         # All data access (UserRepo, ActiveProfileRepo, etc.)
  cache/
    keys.py                 # All Valkey key patterns, TTLs, Lua scripts
    client.py               # ValkeyClient with circuit breaker
  routes/
    chat.py                 # POST /chat, GET /setup, GET /confidence, GET /pets
    background.py           # Fire-and-forget pipeline
    debug.py                # Dev-only inspection endpoints
    simulator.py            # Phase 1 health/food simulators
  jobs/
    nightly.py              # APScheduler: summaries, relationships, suggested questions
  main.py                   # App creation, lifespan, agent init, scheduler, /health
  types.py                  # Shared TypedDict definitions
```

---

## Progress & Task Tracking

All completed work and pending tasks are tracked in `progress.json`. Refer to that file for what has been done and what remains. Design decisions and architecture rationale live in `design-docs/`.

---

## Code Quality Rules

- Every route: `async def` (we make external LLM/API calls)
- Type hints on every function signature
- `logger = logging.getLogger(__name__)` in every module, never `print()`
- All imports at the top of the file, no imports inside functions
- One responsibility per file
- No secrets in code. API keys and endpoints from `.env` only

---

## Security

- API keys -> `.env` only (gitignored)
- `.env.example` has placeholder values
- All secrets loaded through `app/core/config.py`
- CORS: `allow_origins=["*"]` during dev. Lock down before production.
- Auth: `X-User-Code` header required on all endpoints (JWT planned for Phase 4)

---

## How to Run

```bash
# 1. Activate venv
.venv\Scripts\Activate.ps1        # Windows PowerShell
# .venv\Scripts\activate.bat      # Windows CMD

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment
cp .env.example .env              # Fill in API keys + DATABASE_URL

# 4. Start PostgreSQL + Valkey
docker compose up -d

# 5. Run migrations
alembic upgrade head

# 6. Start backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 7. Start frontend (separate terminal)
cd frontend && npm run dev        # http://localhost:5173
```

**Note:** PostgreSQL runs on port 5433 (not 5432) to avoid conflicts.
**Note:** When installing packages, use `.venv/Scripts/pip install <pkg>` so they land in the venv.
**Tip:** If port 8000 seems stuck, run `netstat -ano | findstr :8000` to find stale processes.
