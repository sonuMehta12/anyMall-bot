# AnyMall-chan — Pet Companion Chat AI

A multi-agent pet health chat system powered by LLM. Understands pet context, extracts facts from conversation, and builds a living profile over time. Supports bilingual (English/Japanese), multi-pet conversations, and real pet data from the AALDA API.

## Quick Start

```bash
# 1. Install backend dependencies
cd backend
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env — fill in Azure OpenAI credentials + DATABASE_URL

# 3. Start PostgreSQL (Docker required)
docker compose up -d

# 4. Run database migrations
python -m alembic upgrade head

# 5. Start the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Start the React test UI (separate terminal)
cd frontend
npm install
npm run dev
# Opens on http://localhost:5173
```

## How It Works

```
User message
    -> Thread boundary logic          resolve session_id -> thread_id (24h windows)
    -> IntentClassifier (LLM)         health / food / general + urgency
    -> _detect_language()             Unicode range check -> "EN" or "JA"
    -> Agent 1: Conversation (LLM)    bilingual empathetic response + is_entity + asked_gap_question
    -> Guardrails                     tone + safety checks
    -> Deeplink Builder               redirect payload for health/food intents
    -> Confidence Calculator          score + color (coverage x recency x importance)
    -> Response to user
    |  [fire-and-forget -- user does NOT wait]
    -> Write-through                  persist messages to PostgreSQL
    -> Compaction check               if >= 50 messages, LLM summarization
    -> Agent 2: Compressor (LLM)      extract facts -> fact_log table
    -> Agent 3: Aggregator (rules)    merge facts -> active_profile table
```

## Try These Messages

```
"Node seems tired today"              -> extracts energy_level
"He eats twice a day"                 -> extracts feeding_frequency
"He ate raw food this morning"        -> confirms existing diet_type (boosts confidence)
"Actually he eats kibble"             -> user_correction -> wins over existing value
"Node is vomiting since morning"      -> medical intent -> redirect card
"What food is best for him"           -> nutritional intent -> redirect card
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/chat` | Send a message -- runs full pipeline |
| GET | `/api/v1/pets` | List user's pets from AALDA |
| GET | `/api/v1/confidence?pet_id=149` | Confidence bar score for a pet |
| GET | `/api/v1/debug/facts?pet_id=149` | View extracted facts log |
| GET | `/api/v1/debug/profile?pet_id=149` | View active pet profile |
| GET | `/api/v1/debug/threads` | List all active threads |
| GET | `/api/v1/debug/thread/{id}/messages` | Messages in a thread |
| GET | `/api/v1/simulator/health` | Health module simulator |
| GET | `/api/v1/simulator/food` | Food module simulator |
| GET | `/health` | Liveness check (no /api/v1 prefix) |

Interactive docs: http://localhost:8000/docs

## File Structure

```
AnyMall-chat/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app, CORS, lifespan, /health
│   │   ├── types.py                   # Shared TypedDict (ActiveProfileEntry)
│   │   ├── agents/
│   │   │   ├── intent_classifier.py   # LLM-based intent classification
│   │   │   ├── conversation.py        # Agent 1 -- bilingual conversation
│   │   │   ├── compressor.py          # Agent 2 -- fact extraction (LLM)
│   │   │   ├── aggregator.py          # Agent 3 -- fact merge (rules, no LLM)
│   │   │   └── state.py              # AgentState dataclass
│   │   ├── routes/
│   │   │   ├── chat.py               # POST /chat + GET /confidence + background pipeline
│   │   │   ├── debug.py              # Debug endpoints (facts, profile, threads)
│   │   │   └── simulator.py          # Health/food simulator pages
│   │   ├── services/
│   │   │   ├── guardrails.py         # Safety + tone checks
│   │   │   ├── deeplink.py           # Redirect payload builder
│   │   │   ├── context_builder.py    # Builds pet context from AALDA + DB data
│   │   │   ├── confidence_calculator.py  # Score + color calculation
│   │   │   ├── pet_fetcher.py        # AALDA API client with cache + fallback chain
│   │   │   └── thread_summarizer.py  # LLM summarization for thread compaction
│   │   ├── db/
│   │   │   ├── session.py            # Async engine + session factory
│   │   │   ├── models.py             # ORM: User, ActiveProfile, FactLog, Thread, ThreadMessage
│   │   │   └── repositories.py       # UserRepo, ActiveProfileRepo, FactLogRepo, ThreadRepo, ThreadMessageRepo
│   │   ├── llm/
│   │   │   ├── base.py              # Abstract LLMProvider
│   │   │   ├── azure_openai.py      # Azure OpenAI implementation
│   │   │   └── factory.py           # Provider factory
│   │   └── core/
│   │       └── config.py            # .env -> Settings
│   ├── constants.py                  # Business logic constants
│   ├── requirements.txt
│   ├── docker-compose.yml            # PostgreSQL 16 Alpine (port 5433)
│   ├── alembic.ini                   # Alembic migration config
│   ├── migrations/                   # 5 migration scripts
│   ├── tests/
│   │   └── run_e2e.py               # 59 end-to-end tests (9 sections)
│   └── Dockerfile                    # Production container
├── frontend/                          # React + Vite test UI (dev only)
│   ├── src/
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `users` | Owner relationship data |
| `active_profile` | Current best-known facts per field with confidence |
| `fact_log` | Append-only audit trail of every extracted fact |
| `threads` | 24-hour conversation windows |
| `thread_messages` | Individual messages within threads |

## Architecture

- **LLM Provider**: Strategy pattern -- swap Azure/OpenAI by changing one env var
- **Storage**: PostgreSQL with in-memory hot cache (app.state). Write-through pattern
- **AALDA Integration**: Real pet data fetched per-request, cached 5 min. Fallback chain: cache -> API -> expired cache -> DB -> error
- **Thread Management**: 24h conversation windows with LLM compaction at 50 messages
- **Background Pipeline**: Fire-and-forget -- user never waits for fact extraction

## Current Status

- **Phase 0-2**: Complete (MVP, IntentClassifier, Compressor, Aggregator, PostgreSQL, Threads)
- **API v1**: Complete (versioned endpoints, error contract, redirect restructure)
- **Sprint 2**: Complete (AALDA integration, multi-pet, per-thread locking, fallback chain)
- **Sprint 3**: Complete (language selector EN/JA, production deploy fixes)
- **Code Review**: 31/36 items fixed. Remaining: C4 (dual-pet attribution), W11 (Pet B threads), W18 (users table schema), S8+S9 (frontend test UI)
- **Tests**: 54/59 passing (5 pre-existing flaky/frontend-catch-all)
- **Next**: Phase 3 (nightly batch jobs), HistoryBuilder (ft-013)
