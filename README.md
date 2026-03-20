# AnyMall-chan — Pet Companion Chat AI

A multi-agent pet health chat backend powered by LLM. Extracts facts from conversation, builds a living pet profile over time, and provides bilingual (EN/JA) empathetic responses. Supports multi-pet conversations and real pet data from the AALDA platform.

## Prerequisites

- **Python** 3.11+
- **Docker** (for PostgreSQL and Valkey)
- **OpenAI API key** (or Azure OpenAI credentials)

## Quick Start

```bash
# 1. Clone and enter the backend directory
cd backend

# 2. Create and activate virtual environment
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows CMD:
.venv\Scripts\activate.bat
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env — fill in your OpenAI API key + DATABASE_URL

# 5. Start PostgreSQL + Valkey (Docker required)
docker compose up -d

# 6. Run database migrations
python -m alembic upgrade head

# 7. Start the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 8. (Optional) Start the React test UI
cd ../frontend
npm install && npm run dev
# Opens on http://localhost:5173
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | No | `openai` | `"openai"` or `"azure"` |
| `OPENAI_API_KEY` | Yes (if openai) | — | OpenAI API key |
| `OPENAI_MODEL_CHAT` | No | `gpt-4.1` | Model name |
| `DATABASE_URL` | Yes | — | PostgreSQL async connection string |
| `VALKEY_URL` | No | `valkey://:valkey_dev@localhost:6379/0` | Valkey (Redis-compatible) cache |
| `AALDA_API_URL` | No | `https://anymall-api.stagingapp.in/api/v1` | AALDA platform API |

To use Azure OpenAI instead, set `LLM_PROVIDER=azure` and configure the `AZURE_OPENAI_*` variables. See `.env.example` for details.

## Architecture

```
User message + X-User-Code header + pet_ids[]
    |
    v
POST /api/v1/chat
    |
    +-- AALDA fetch (parallel)         pet data with 5-min Valkey cache
    +-- Thread boundary                24h conversation windows
    +-- IntentClassifier (LLM)         health / food / general + urgency
    +-- Language detection              3-step: request > DB stored > auto-detect
    +-- Agent 1: Conversation (LLM)    bilingual empathetic response
    +-- Guardrails                      tone + safety checks
    +-- Confidence calculator           score + color
    +-- Response to user
    |
    |  [fire-and-forget — user does NOT wait]
    |
    +-- Write-through                  messages -> PostgreSQL + Valkey
    +-- Compaction check               >= 50 messages -> LLM summarization
    +-- Agent 2: Compressor (LLM)      extract facts -> fact_log table
    +-- Agent 3: Aggregator (rules)    merge facts -> active_profile table
    +-- HistoryBuilder (LLM)           fact_log -> pet health narrative
```

### Key Design Patterns

- **LLM Provider**: Strategy pattern — swap OpenAI/Azure by changing one env var. Zero code changes.
- **Storage**: PostgreSQL (source of truth) + Valkey (hot cache). Write-through rule: DB first, then cache.
- **AALDA Integration**: Read-only from AALDA platform. Pet data cached in Valkey (5 min TTL). No write-back — our `active_profile` handles chat-learned facts.
- **Background Pipeline**: Fire-and-forget after user gets their reply. Compressor + Aggregator + HistoryBuilder run asynchronously.
- **Valkey**: Circuit breaker (5 failures -> 30s open). TTL jitter prevents thundering herd. Graceful degradation when Valkey is down.

## API Endpoints

### Production Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/chat` | X-User-Code | Send a message, get AI reply |
| `GET` | `/api/v1/pets` | X-User-Code | List user's pets |
| `GET` | `/api/v1/confidence?pet_id=149` | X-User-Code | Confidence bar score |
| `GET` | `/health` | None | Liveness check |

### Debug Endpoints (development only)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/debug/facts?pet_id=149` | Extracted facts log |
| `GET` | `/api/v1/debug/profile?pet_id=149` | Active pet profile |
| `GET` | `/api/v1/debug/threads` | Active threads |
| `GET` | `/api/v1/debug/thread/{id}/messages` | Messages in a thread |
| `GET` | `/api/v1/debug/user?user_code=XXX` | User record |
| `POST` | `/api/v1/debug/trigger_nightly` | Run nightly jobs manually |
| `POST` | `/api/v1/debug/trigger_summarizer` | Run thread summarizer |

Interactive docs: http://localhost:8000/docs

### Chat Request Example

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Code: 3AOU9K1PWH" \
  -d '{
    "message": "Node seems tired today",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "pet_ids": [149],
    "language": "auto",
    "display_name": "Shara"
  }'
```

See [design-docs/api-flutter-handover.md](backend/design-docs/api-flutter-handover.md) for full API documentation.

## Project Structure

```
AnyMall-chat/
├── backend/
│   ├── app/
│   │   ├── main.py                       # FastAPI app, CORS, lifespan, APScheduler
│   │   ├── types.py                      # Shared TypedDict + Protocol definitions
│   │   ├── agents/
│   │   │   ├── intent_classifier.py      # LLM-based intent classification
│   │   │   ├── conversation.py           # Agent 1 — bilingual conversation
│   │   │   ├── compressor.py             # Agent 2 — fact extraction (LLM)
│   │   │   ├── aggregator.py             # Agent 3 — fact merge (rules, no LLM)
│   │   │   └── state.py                  # AgentState dataclass
│   │   ├── routes/
│   │   │   ├── chat.py                   # POST /chat + GET /confidence + GET /pets
│   │   │   ├── background.py             # Fire-and-forget pipeline
│   │   │   ├── debug.py                  # Debug endpoints
│   │   │   └── simulator.py              # Health/food simulator pages
│   │   ├── services/
│   │   │   ├── guardrails.py             # Safety + tone checks
│   │   │   ├── deeplink.py               # Redirect payload builder
│   │   │   ├── context_builder.py        # 3-layer merge: AALDA + chat + identity
│   │   │   ├── confidence_calculator.py  # Score + color calculation
│   │   │   ├── pet_fetcher.py            # AALDA API client with Valkey cache
│   │   │   ├── thread_summarizer.py      # LLM compaction (HEALTH CONTEXT + USER STYLE)
│   │   │   ├── history_builder.py        # fact_log -> pet health narrative
│   │   │   └── relationship_builder.py   # USER STYLE -> relationship_summary
│   │   ├── jobs/
│   │   │   └── nightly.py                # APScheduler nightly jobs (midnight UTC)
│   │   ├── cache/
│   │   │   ├── keys.py                   # Cache key patterns, TTLs, Lua scripts
│   │   │   └── client.py                 # ValkeyClient with circuit breaker
│   │   ├── db/
│   │   │   ├── session.py                # Async engine + session factory
│   │   │   ├── models.py                 # SQLAlchemy ORM models
│   │   │   └── repositories.py           # Data access layer
│   │   ├── llm/
│   │   │   ├── base.py                   # Abstract LLMProvider
│   │   │   ├── openai_provider.py        # Direct OpenAI implementation
│   │   │   ├── azure_openai.py           # Azure OpenAI implementation
│   │   │   └── factory.py                # Provider factory
│   │   └── core/
│   │       └── config.py                 # .env -> Settings (pydantic-settings)
│   ├── constants.py                      # Business logic constants + GAP_PRIORITY_LADDER
│   ├── requirements.txt
│   ├── docker-compose.yml                # PostgreSQL 16 + Valkey 8
│   ├── alembic.ini
│   ├── migrations/                       # Alembic migration scripts
│   ├── design-docs/                      # Architecture and design documents
│   ├── tests/
│   │   ├── run_e2e.py                    # 70 end-to-end tests (11 sections)
│   │   ├── test_valkey.py                # 17 Valkey integration tests
│   │   └── test_sprint6.py              # 21 background pipeline tests
│   └── Dockerfile                        # Production container
├── frontend/                             # React + Vite test UI (dev only, NOT production)
└── README.md
```

## Database Tables

All tables use the `anymall_chan_` prefix for shared database deployment.

| Table | Purpose |
|---|---|
| `anymall_chan_users` | Owner relationship data + communication preferences |
| `anymall_chan_active_profile` | Current best-known facts per field with confidence scores |
| `anymall_chan_fact_log` | Append-only audit trail of every extracted fact |
| `anymall_chan_threads` | 24-hour conversation windows |
| `anymall_chan_thread_messages` | Individual messages within threads (FK to threads) |

All tables include `user_code` for direct user-level queries.

## Testing

All tests require the backend server running at `localhost:8000` with PostgreSQL and Valkey up.

```bash
# Start infrastructure
docker compose up -d
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run all test suites
python tests/run_e2e.py          # 70 E2E tests (11 sections)
python tests/test_sprint6.py     # 21 background pipeline tests
python tests/test_valkey.py      # 17 Valkey integration tests
```

## Try These Messages

```
"Node seems tired today"              -> extracts energy_level
"He eats twice a day"                 -> extracts feeding_frequency
"He ate raw food this morning"        -> confirms existing diet (boosts confidence)
"Actually he eats kibble"             -> user_correction -> wins over existing value
"Node is vomiting since morning"      -> medical intent -> redirect card
"What food is best for him"           -> nutritional intent -> redirect card
```

## Documentation

- [API Flutter Handover](backend/design-docs/api-flutter-handover.md) — Full API contract for Flutter team
- [AALDA Integration](backend/design-docs/aalda-integration.md) — How we read pet data from AALDA
- [AALDA DB Alignment](backend/design-docs/aalda-db-alignment-questions.md) — Decision record: no write-back
- [Valkey Design](backend/design-docs/valkey-design.md) — Cache layer architecture
- [System Design](backend/design-docs/system-design.md) — Overall system architecture
- [Build Journal](backend/notes.md) — Detailed notes on what was built and why

## License

Private — AnyMall internal use only.
