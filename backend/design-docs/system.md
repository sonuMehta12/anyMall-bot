# AnyMall-chan Backend — System Decisions

This document records every significant architectural and technical decision made during
the backend build. Each decision includes the context (why we needed to decide), the
decision itself, why we made it, what alternatives we considered, and the consequences.

This is a living document. Every major decision gets added here before code is written.

---

## Decision Log

---

### SD-001: Python + FastAPI as the Application Framework

**Date:** 2026-03-07
**Status:** Decided

**Context:**
We need a backend framework for a REST API consumed by a Flutter iOS app. The system
makes external LLM calls (Azure OpenAI) and database operations. The entire system design
document is already written in Python pseudocode.

**Decision:**
Python 3.12 + FastAPI.

**Rationale:**
- The system design is written entirely in Python pseudocode — direct translation, no mental mapping overhead
- FastAPI is async-first: critical for an app that makes concurrent LLM + DB calls
- FastAPI uses Pydantic for request/response validation — all LLM JSON outputs get validated automatically
- FastAPI auto-generates OpenAPI/Swagger docs — the Flutter team can test endpoints without coordination
- Best-in-class LLM SDK support: both `openai` and `anthropic` SDKs have native async Python support
- `uvicorn` (ASGI server) handles concurrency efficiently for I/O-bound workloads

**Alternatives Considered:**
- Node.js/TypeScript + Express: Good LLM SDK support, but the team is building in Python and the design is in Python
- Django + DRF: Too heavyweight for an API-only service. Django's ORM is sync-first (async support is newer and rougher)
- Flask: Sync-first, no built-in async. Would require manual async workarounds

**Consequences:**
- All route handlers MUST be `async def` — this is enforced as a project rule
- We use `asyncpg` or SQLAlchemy async for DB, `aioredis` for Redis
- The test runner is `pytest` with `pytest-asyncio`

---

### SD-002: Repository Pattern for Database Abstraction

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The developer currently uses a personal PostgreSQL database. Later, the project will
migrate to a company database. The prototype used a global in-memory `InMemoryStore`
imported everywhere, making it impossible to swap the database without touching every file.

**Decision:**
Repository Pattern. Each DB table has an abstract base class (interface) in `app/db/base.py`.
PostgreSQL implementations live in `app/db/postgres/repositories/`. Business logic (agents,
services) only interacts with abstract repository classes — never with SQLAlchemy or any
DB driver directly.

**Rationale:**
- Swapping databases = implementing the same interface, changing one line in `dependencies.py`
- Agents and services become testable in isolation (inject a mock repository in tests)
- Forces clear separation between "what data operations are needed" (interface) and "how they work" (implementation)
- Matches the swap-ability goal stated explicitly by the developer

**Alternatives Considered:**
- Active Record pattern (Django-style): Business objects contain their own DB logic. Hard to swap, hard to test.
- Direct SQLAlchemy in routes: Even harder to swap. Routes become coupled to ORM details.
- Keeping the global store pattern: Works for prototype, fails for production and testing

**The Interface Rule:**
```python
# CORRECT: agent receives repo, knows nothing about SQLAlchemy
async def run(self, pet_id: str, profile_repo: AbstractActiveProfileRepo) -> ...:
    facts = await profile_repo.get_by_pet_id(pet_id)

# WRONG: agent imports and calls DB directly
async def run(self, pet_id: str, db: AsyncSession) -> ...:
    result = await db.execute(select(ActiveProfileORM).where(...))
```

**Consequences:**
- More files upfront (interfaces + implementations) but every layer is independently swappable
- Must write `to_domain()` and `from_domain()` converters on ORM models to keep domain models clean
- Company DB migration = write new repository implementations, zero agent/service changes

---

### SD-003: Two-Layer Model System (ORM Models + Domain Models)

**Date:** 2026-03-07
**Status:** Decided

**Context:**
Without domain models, all data between layers flows as untyped `dict`. This means no IDE
autocompletion, no validation, and no guaranteed contract between components. The prototype
had this problem — everything was `dict` and errors only appeared at runtime.

**Decision:**
Two separate model layers:
- **ORM models** (`app/db/postgres/models/`) — SQLAlchemy classes. Only the repository implementations touch these.
- **Domain models** (`app/domain/`) — Pure Python `@dataclass` classes. Agents and services use these.

ORM models have `to_domain()` (convert ORM row → domain object) and `from_domain()` (domain → ORM row) methods.

**Rationale:**
- Domain models have zero imports from SQLAlchemy or any DB driver — they can be used anywhere
- Agents receive typed `Pet`, `ProfileFact`, `ExtractedFact` objects, not raw `dict`
- IDE autocompletion works correctly. Bugs are caught at type-check time, not runtime.
- If we swap DB: the ORM models change, domain models do NOT change, agents/services are untouched

**Alternatives Considered:**
- Pydantic models for everything: Pydantic is great for HTTP validation but heavier than dataclasses for internal domain objects. Also, SQLAlchemy ORM and Pydantic require extra bridge code (`model_validate`).
- Just use `TypedDict`: No validation. Better than plain `dict` but weaker than dataclasses.
- Single model (SQLAlchemy model passed everywhere): Tight coupling. Impossible to swap DB.

**Example:**
```python
# app/domain/pet.py — pure Python, no DB imports
@dataclass
class Pet:
    id: str
    name: str
    species: str
    breed: str
    life_stage: str

# app/db/postgres/models/pet.py — SQLAlchemy ORM
class PetORM(Base):
    __tablename__ = "pets"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)

    def to_domain(self) -> Pet:
        return Pet(id=self.id, name=self.name, ...)
```

**Consequences:**
- Extra conversion step in each repository method (`to_domain()` before returning)
- Domain models must be updated if new fields are added to DB (but this is explicit and intentional)

---

### SD-004: LLM Provider Strategy Pattern (Swappable LLM)

**Date:** 2026-03-07
**Status:** Decided

**Context:**
Currently using Azure OpenAI. The developer wants to switch to direct OpenAI (or another
provider) later without rewriting agent code. The prototype had two separate LLM ABC classes
(`LLMConversationProvider` in `conversation_agent.py` and `LLMExtractorProvider` in
`compressor.py`) which is duplication.

**Decision:**
One unified abstract base class `LLMProvider` in `app/llm/base.py` with two methods:
- `complete(messages: list[dict], max_tokens: int) -> str` — returns raw text
- `complete_json(messages: list[dict], max_tokens: int) -> dict` — returns parsed JSON dict

A factory `app/llm/factory.py` reads `LLM_PROVIDER` env var and returns the right implementation.
Agents receive an `LLMProvider` instance via FastAPI `Depends()` — they never construct clients.

**Rationale:**
- Both Agent 1 and Agent 2 need LLM calls — sharing one interface eliminates duplication
- Agents are completely decoupled from which LLM provider is used
- Switching providers = change env var only. No agent code changes.
- The factory pattern centralizes provider-specific initialization (endpoints, API versions, deployments)

**Provider Implementations:**
- `app/llm/azure_openai.py` — current. Uses `openai.AzureOpenAI` with endpoint + api_version
- `app/llm/openai_provider.py` — future. Uses `openai.AsyncOpenAI` with just api_key

**Configuration:**
```
LLM_PROVIDER=azure

# Azure-specific
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_DEPLOYMENT_CHAT=gpt-4.1        # Agent 1 (quality-critical)
AZURE_OPENAI_DEPLOYMENT_FAST=gpt-4.1        # Agent 2 (can downgrade later)
```

**Consequences:**
- Agent 1 and Agent 2 use the same `LLMProvider` type — they may use different model deployment names
- The provider is responsible for retry logic and error handling — agents just receive results or exceptions
- Mock provider still exists for offline development/testing — inject `MockLLMProvider` instead

---

### SD-005: Dependency Injection via FastAPI `Depends()`

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The prototype created agent instances as global variables at module level in `main.py`.
This means: no way to swap implementations per-request, no way to inject test doubles,
and all state is shared.

**Decision:**
All dependencies (DB session, repositories, LLM provider, agents) are provided via
FastAPI's `Depends()` system. All wiring is centralized in `app/core/dependencies.py`.

**Structure:**
```
app/core/dependencies.py
  get_settings()           -> Settings
  get_db_session()         -> AsyncSession
  get_llm_provider()       -> LLMProvider
  get_pet_repo()           -> AbstractPetRepo
  get_active_profile_repo() -> AbstractActiveProfileRepo
  get_fact_log_repo()      -> AbstractFactLogRepo
  get_conversation_repo()  -> AbstractConversationRepo
  get_redis_client()       -> Redis
```

**Rationale:**
- One place to see all dependencies and their implementations
- Switching database = change one function in `dependencies.py`
- Switching LLM = change one function (or just the env var) in `dependencies.py`
- Tests inject mocks by overriding `app.dependency_overrides[get_pet_repo] = mock_repo`
- No global state — each request gets fresh dependencies (or scoped singletons as appropriate)

**Consequences:**
- Routes have more parameters (each `Depends()` shows up in the signature)
- This is intentional: explicit dependencies are better than hidden global state

---

### SD-006: Synchronous vs Asynchronous — All Routes Are Async

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The prototype used synchronous route handlers (`def chat()`, not `async def chat()`).
With a synchronous route, every LLM call blocks the entire uvicorn worker thread.
This means only one request can be processed at a time per worker.

**Decision:**
All FastAPI route handlers are `async def`. All DB operations use async SQLAlchemy (`AsyncSession`).
All Redis operations use `aioredis`. All LLM calls use the async OpenAI SDK (`AsyncAzureOpenAI`).

**Rationale:**
- With `async def`, uvicorn can handle other requests while waiting for LLM response (I/O-bound)
- An LLM call takes 1-5 seconds. Blocking during that time for a sync route is unacceptable at any scale
- The Agent 2+3 pipeline runs async after Agent 1 responds — user gets response immediately

**The Pattern:**
```python
@router.post("/chat")
async def chat(req: ChatRequest, ...):
    # 1. Generate response (await LLM call)
    response = await agent1.run(context)
    # 2. Return response to user IMMEDIATELY
    # 3. Fire-and-forget fact extraction (does not block response)
    asyncio.create_task(run_extraction_pipeline(req.message, req.pet_id))
    return ChatResponse(...)
```

**Consequences:**
- Tests must use `pytest-asyncio` and `async def test_...()` for async code
- SQLAlchemy must use `AsyncSession`, not the standard `Session`
- The `asyncio.create_task()` pattern for background work means errors in Agent 2/3 do NOT
  crash the user's request — they are logged separately

---

### SD-007: Fact Extraction is Fire-and-Forget (Does Not Block Chat Response)

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The system design specifies Phases 1-2 (response generation + guardrails) happen before
returning to the user, but Phases 3-5 (fact extraction, aggregation, cache update) happen
asynchronously after the response is sent. The prototype runs all phases synchronously.

**Decision:**
After Agent 1 generates a response and it passes guardrails, the response is immediately
returned to the user. Agent 2 (Compressor) and Agent 3 (Aggregator) run as an
`asyncio.create_task()` — a background coroutine that does not block the HTTP response.

**Rationale:**
- User experience: the chat feels instant. Fact extraction (LLM call) takes 1-2 extra seconds
- The pet profile update is eventually consistent — it's fine if the profile updates 2 seconds later
- If Agent 2 fails, the user already has their response. No cascading failures.

**Consequences:**
- Background task errors must be logged carefully — they won't surface as HTTP errors
- The confidence bar score update happens async — the response includes the PREVIOUS score
- The confidence bar can be updated via a WebSocket push or next request (acceptable)

---

### SD-008: Constants vs Environment Config — Two Separate Files

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The prototype's `config.py` mixed two very different things: business logic constants
(PRIORITY_RANKS, ENTITY_PATTERNS, confidence weights) and a description of what
environment variables should exist. This caused confusion about what is a "secret"
vs what is a "business rule."

**Decision:**
Two files with strict separation:

1. `constants.py` — Business logic constants only. No secrets. Safe to commit to git.
   - PRIORITY_RANKS, RANK_ORDER, ALL_PRIORITY_KEYS_ORDERED
   - ENTITY_PATTERNS, MEDICAL_KEYWORDS, BLOCKED_MEDICAL_JARGON
   - Confidence weights, recency decay table, session limits, field labels, gap hints

2. `app/core/config.py` — Environment configuration only. Uses `pydantic-settings`.
   - API keys, endpoints, DB connection strings, Redis URL
   - Loaded from `.env` file. Never hardcoded. Never committed.

**Rule:** If it has a value that would be the same in every environment (dev, staging, prod)
→ it goes in `constants.py`. If it changes per environment → it goes in `config.py` + `.env`.

**Consequences:**
- `constants.py` is the direct rename of the existing `config.py` (content stays the same)
- All modules that currently `from backend.config import X` get updated to `from constants import X`
- Secrets move to `app/core/config.py` read from `.env`

---

### SD-009: Database — PostgreSQL with Alembic Migrations

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The prototype uses an in-memory dict store. We need a real persistent database.
The system design already specifies PostgreSQL with the exact table schemas.

**Decision:**
PostgreSQL via SQLAlchemy (async) with Alembic for schema migrations.

**Schema:**
```sql
-- Five tables (see system-design.md for full column specs)
pets                 -- identity, set at signup
fact_log             -- append-only audit trail of every extracted fact
active_profile       -- current best-known value per (pet_id, key)
conversation_log     -- append-only record of every chat message
compressed_history   -- NL summaries of past sessions (session_compact + longitudinal)
```

**Why Alembic:**
- Version-controlled schema changes — every change is a numbered migration file
- Can upgrade/downgrade schema without data loss
- Works with both personal DB and company DB (same migration files, different `DATABASE_URL`)

**DB URL Configuration:**
```
# Personal dev DB
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/anymallchan

# Company DB (just change this value, no code changes)
DATABASE_URL=postgresql+asyncpg://company_user:pass@company-host:5432/prod_db
```

**Consequences:**
- All ORM models inherit from a shared `Base = declarative_base()`
- Alembic `env.py` must point to this `Base` to auto-detect model changes
- Always run `alembic upgrade head` before starting the server

---

### SD-010: Redis for Caching (Cache-Aside Pattern)

**Date:** 2026-03-07
**Status:** Decided

**Context:**
Every chat message needs the pet's active profile as context for Agent 1. Reading all
profile rows from Postgres on every message adds DB load and latency.

**Decision:**
Redis cache with cache-aside pattern and explicit TTLs:
- `pet:{id}:active_profile` → JSON string of all profile rows, **TTL: 1 hour**
- `pet:{id}:profile_summary` → NL summary for external modules, **TTL: 24 hours**

Cache-aside means: try Redis first. If miss, load from Postgres, write to Redis, return.
On profile update (after Aggregator runs): invalidate both keys for that pet.

**Rationale:**
- Profile data is read on every message, written rarely (only when new facts extracted)
- Target: < 100ms profile retrieval for Agent 1 context building (Redis: ~1ms vs Postgres: 10-50ms)
- TTL prevents stale data accumulating if cache invalidation ever misses

**Consequences:**
- Profile updates must always invalidate the cache keys — this must happen in the Aggregator pipeline
- Redis is a soft dependency in development: if Redis is down, fall through to Postgres (don't crash)

---

### SD-011: The Aggregator Has No LLM — Deterministic Logic Only

**Date:** 2026-03-07 (from system-design.md)
**Status:** Decided (carried from design)

**Context:**
Conflict resolution (which fact wins when two conflict) could theoretically be done by an LLM.
The system design explicitly chose not to do this.

**Decision:**
Agent 3 (Aggregator) is pure Python deterministic logic. No LLM call. The interface is designed
so an LLM reasoning model could replace it later with zero changes to the surrounding pipeline.

**Conflict Resolution Rules (in priority order):**
1. `source == "user_correction"` → always wins
2. New value == existing value → confirmation: boost confidence +0.05, update timestamp
3. New confidence < (existing confidence × 0.8) → new fact loses (still logged)
4. New confidence > existing confidence → new fact wins
5. Equal confidence → newer wins (new fact is always newer)
6. True conflict (both plausible) → keep existing, flag for human review

**Rationale:**
- Deterministic = fast, free, predictable, testable
- An LLM call for every fact conflict would add latency and cost to every message
- The rules handle 99% of real-world cases correctly
- The `flag_conflict` escape hatch handles the remaining 1%

**Consequences:**
- The Aggregator class has a single `run(pet_id, new_facts, profile_repo, fact_log_repo)` method
- All six rules are unit-testable without any LLM mock
- The interface for the Aggregator does not change if we add an LLM later

---

### SD-012: Session Compaction and Longitudinal Summaries

**Date:** 2026-03-07 (from system-design.md)
**Status:** Decided (carried from design)

**Context:**
Agent 1 receives conversation history as context. Sending the full raw conversation log
would quickly exceed the LLM's context window. We need to compress old sessions.

**Decision:**
Two separate summary types stored in `compressed_history` table, distinguished by `summary_type`:

| `summary_type` | What it covers | Rebuilt when |
|---|---|---|
| `session_compact` | Last 5-10 sessions | After every N sessions (batch job) |
| `longitudinal` | Last 3-12 months of fact trends | Weekly/monthly batch job |

Agent 1 receives the latest of EACH type — one for recent context, one for long-term patterns.

**Why not store in `active_profile`?**
`active_profile` is structured key-value facts (current state). Mixing an LLM-generated
narrative into it breaks its structure. `compressed_history` is already the correct home for NL summaries.

**Consequences:**
- Background batch job is needed for compaction (Phase 5)
- `compressed_history` table has a `summary_type` column
- Query pattern: `SELECT DISTINCT ON (summary_type) ... ORDER BY summary_type, created_at DESC`

---

### SD-013: Error Handling Strategy

**Date:** 2026-03-07
**Status:** Decided

**Context:**
The prototype had `try/except` blocks in individual agent methods but no consistent strategy.
Some errors crashed the request, others silently fell back to mock responses.

**Decision:**
Layered error handling:

1. **LLM Provider**: Retry up to 3 times with exponential backoff. If all retries fail, raise `LLMProviderError`.
2. **Agent 1 (Conversation)**: Catch `LLMProviderError`. Fall back to a safe static response ("I'm having trouble right now, please try again~"). Never crash the chat.
3. **Agent 2 (Compressor)**: Catch any error. Return `[]` (empty facts). Never crash the chat response.
4. **Agent 3 (Aggregator)**: Deterministic logic — should not fail. If it does, log and skip the fact.
5. **Routes**: Only raise `HTTPException` for client errors (404 not found, 400 bad input). Never for internal errors.

**Rule:** A failed fact extraction must NEVER cause the user's chat response to fail.
The user's response is decoupled from the background pipeline.

**Consequences:**
- Custom exception classes: `LLMProviderError`, `RepositoryError`
- Background tasks log errors to `logger.error()` — these show in server logs but not user-facing
- Add Sentry or similar error tracking in production for background task failures

---

## Technical Standards

### Python Version
Python 3.12 (uses `X | None` union syntax, `match` statements, improved type hints)

### Required Packages
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
pydantic-settings>=2.0.0       # For settings from .env
sqlalchemy[asyncio]>=2.0.0     # Async SQLAlchemy
asyncpg>=0.29.0                # PostgreSQL async driver
alembic>=1.13.0                # DB migrations
redis>=5.0.0                   # Redis async client (aioredis is now merged into redis-py)
openai>=1.30.0                 # Azure OpenAI + direct OpenAI (same SDK)
python-dotenv>=1.0.0
python-jose[cryptography]>=3.3.0  # JWT (Phase 3: auth)
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0                  # Async test client for FastAPI
```

### Naming Conventions
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions and variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE` (in `constants.py`)
- DB table names: `snake_case` (e.g., `active_profile`, `fact_log`)
- Environment variables: `UPPER_SNAKE_CASE` (e.g., `AZURE_OPENAI_API_KEY`)

### Async Rules
- Every route handler: `async def`
- Every repository method: `async def`
- Every LLM provider method: `async def`
- Background tasks: `asyncio.create_task()`
- Never use `time.sleep()` — use `asyncio.sleep()` for delays in async context

### Logging Rules
- Every module: `logger = logging.getLogger(__name__)`
- Never use `print()` in production code
- Log levels: DEBUG (details), INFO (pipeline steps), WARNING (fallback used), ERROR (unexpected failure)
- Log format: set in `main.py` once, applies to all loggers
