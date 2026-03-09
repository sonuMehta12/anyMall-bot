# AnyMall-chan — API Endpoints Reference

Last updated: 2026-03-09

Complete list of all API endpoints — built and planned.
Each endpoint shows its status, request/response format, and which phase introduces it.

---

## Summary Table

| # | Method | Path | Status | Phase | Consumer |
|---|--------|------|--------|-------|----------|
| 1 | POST | `/chat` | Built | 0 | Flutter app, React test UI |
| 2 | GET | `/health` | Built | 0 | Load balancer, monitoring |
| 3 | GET | `/debug/facts` | Built | 1B | Dev tools, React console |
| 4 | GET | `/debug/profile` | Built | 1B | Dev tools, React console |
| 5 | GET | `/health/chat` | Built | 1A | Browser (Phase 1 simulator) |
| 6 | GET | `/food/chat` | Built | 1A | Browser (Phase 1 simulator) |
| 7 | GET | `/api/v1/pet/{pet_id}/context` | Planned | 1C | Health module, Food module |
| 8 | POST | `/api/v1/auth/login` | Planned | 4 | Flutter app |
| 9 | POST | `/api/v1/auth/refresh` | Planned | 4 | Flutter app |
| 10 | GET | `/api/v1/pet/{pet_id}/profile` | Planned | 1C | Flutter app (profile screen) |
| 11 | PUT | `/api/v1/pet/{pet_id}/profile` | Planned | 1C | Flutter app (onboarding edit) |
| 12 | GET | `/api/v1/pet/{pet_id}/facts` | Planned | 1C | Flutter app (fact history) |
| 13 | POST | `/api/v1/pet` | Planned | 1C | Flutter app (onboarding) |
| 14 | GET | `/api/v1/sessions/{session_id}` | Planned | 2 | Flutter app (conversation history) |

---

## Built Endpoints (Phase 0 — 1B)

---

### 1. POST /chat

**The core endpoint.** Sends a user message through the full agent pipeline and returns a reply.

**Route file:** `app/routes/chat.py`
**Auth:** None (Phase 4)
**Rate limit:** None (Phase 4)

**Request:**
```json
{
  "message": "Luna weighs about 4kg now",
  "session_id": "session-abc123"
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `message` | string | yes | 1-4000 chars | User's message text |
| `session_id` | string | yes | 1-128 chars | Unique ID for conversation continuity |

**Response (200):**
```json
{
  "message": "That's a healthy weight for a Shiba Inu! ...",
  "redirect": null,
  "session_id": "session-abc123",
  "questions_asked_count": 0,
  "was_guardrailed": false,
  "is_entity": true,
  "intent_type": "general",
  "urgency": "low",
  "confidence_score": 42,
  "confidence_color": "red"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | Agent 1's reply (guardrailed) |
| `redirect` | object / null | Present only for health/food intents (see below) |
| `session_id` | string | Echo of input session_id |
| `questions_asked_count` | int | How many questions Agent 1 asked this turn |
| `was_guardrailed` | bool | True if guardrails modified the reply |
| `is_entity` | bool | True if Agent 1 detected extractable pet facts |
| `intent_type` | string | `"general"` / `"health"` / `"food"` |
| `urgency` | string | `"low"` / `"medium"` / `"high"` |
| `confidence_score` | int | 0-100, how well the system knows the pet |
| `confidence_color` | string | `"green"` (80-100) / `"yellow"` (50-79) / `"red"` (0-49) |

**Redirect payload (when `intent_type` is `"health"` or `"food"`):**
```json
{
  "redirect": {
    "module": "health",
    "deep_link": "http://localhost:8000/health/chat?query=Luna+has+been+vomiting",
    "pre_populated_query": "Luna has been vomiting since morning",
    "pet_summary": "Luna is a 2-year-old female Shiba Inu on a raw food diet...",
    "urgency": "high"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `module` | string | `"health"` or `"food"` |
| `deep_link` | string | URL for the Flutter app to navigate to |
| `pre_populated_query` | string | User's original message (pre-fill in target module) |
| `pet_summary` | string | Full pet context so the target module needs no extra lookup |
| `urgency` | string | `"high"` / `"medium"` / `"low"` — controls UI styling |

**Background side effects:**
After the response is sent, a fire-and-forget background pipeline runs:
1. Compressor (Agent 2) extracts facts from the message → writes to `fact_log`
2. Aggregator (Agent 3) merges high-confidence facts → updates `active_profile`

The user never waits for this. Results are visible via `/debug/facts` and `/debug/profile` after ~5-8 seconds.

**Error responses:**
| Status | Condition |
|--------|-----------|
| 422 | Invalid request body (missing fields, too long, etc.) |
| 503 | Agent not initialized yet (server still starting) |

---

### 2. GET /health

Liveness check. Used by load balancers and monitoring tools.

**Route file:** `app/main.py`

**Response (200):**
```json
{
  "status": "ok",
  "llm_provider": "azure",
  "llm_reachable": true,
  "phase": "0"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` if server is up |
| `llm_provider` | string | Which LLM provider is configured |
| `llm_reachable` | bool | True if a test LLM call succeeded |
| `phase` | string | Current development phase |

**Known issue:** Makes a real Azure OpenAI API call on every ping. Will be cached with 60s TTL in Phase 2 (see `security.md` S-09).

---

### 3. GET /debug/facts

Returns extracted facts from the Compressor (Agent 2).

**Route file:** `app/routes/debug.py`

**Query parameters:**

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `session_id` | string | no | — | Filter facts to one session |
| `limit` | int | no | 20 | Max entries returned (1-100) |

**Response (200):**
```json
{
  "status": "ok",
  "count": 2,
  "session_id_filter": "session-abc123",
  "facts": [
    {
      "key": "weight",
      "value": "4 kg",
      "confidence": 0.75,
      "source_rank": "explicit_owner",
      "time_scope": "current",
      "source_quote": "Luna weighs about 4kg now",
      "needs_clarification": false,
      "session_id": "session-abc123",
      "extracted_at": "2026-03-08T14:30:00Z"
    },
    {
      "key": "appetite",
      "value": "reduced",
      "confidence": 0.60,
      "source_rank": "inferred",
      "time_scope": "current",
      "source_quote": "she's been eating less lately",
      "needs_clarification": true,
      "session_id": "session-abc123",
      "extracted_at": "2026-03-08T14:30:00Z"
    }
  ]
}
```

**Fact fields:**

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Profile field name (e.g., `"weight"`, `"diet_type"`, `"allergies"`) |
| `value` | string | Extracted value |
| `confidence` | float | 0.0-1.0 — how confident the Compressor is |
| `source_rank` | string | `"explicit_owner"` / `"vet_confirmed"` / `"inferred"` / `"user_correction"` |
| `time_scope` | string | `"current"` / `"past"` / `"unknown"` |
| `source_quote` | string | Exact text from the user message |
| `needs_clarification` | bool | True if confidence is 0.50-0.70 (low band) |
| `session_id` | string | Which session this fact came from |
| `extracted_at` | string | ISO 8601 timestamp |

---

### 4. GET /debug/profile

Returns the current active profile — the Aggregator's (Agent 3) merged output.

**Route file:** `app/routes/debug.py`

**Response (200):**
```json
{
  "status": "ok",
  "field_count": 7,
  "profile": {
    "weight": {
      "value": "4 kg",
      "confidence": 0.75,
      "source_rank": "explicit_owner",
      "status": "new",
      "updated_at": "2026-03-08T14:30:00Z",
      "session_id": "session-abc123"
    },
    "diet_type": {
      "value": "raw food",
      "confidence": 0.80,
      "source_rank": "explicit_owner",
      "status": "confirmed",
      "updated_at": "2026-03-08T10:00:00Z"
    }
  }
}
```

**Profile entry fields:**

| Field | Type | Description |
|-------|------|-------------|
| `value` | string | Current best-known value for this field |
| `confidence` | float | 0.0-1.0 — aggregated confidence |
| `source_rank` | string | Source of this value |
| `status` | string | `"new"` / `"confirmed"` / `"updated"` / `"conflict"` |
| `change_detected` | string / null | What changed (e.g., `"4kg → 4.5kg"`) |
| `updated_at` | string | When this entry was last written |
| `session_id` | string | Which session last updated this entry |

---

### 5. GET /health/chat

Phase 1 simulator page for the Health module. Renders an HTML page showing what a real Health module would receive when a user is redirected.

**Route file:** `app/routes/simulator.py`

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `query` | string | Pre-populated user query |
| `urgency` | string | `"high"` / `"medium"` / `"low"` |

**Response:** HTML page (not JSON). For browser testing only. Will be replaced by the real Health module in production.

---

### 6. GET /food/chat

Same as `/health/chat` but for the Food module simulator.

**Route file:** `app/routes/simulator.py`

---

## Planned Endpoints

---

### 7. GET /api/v1/pet/{pet_id}/context — Context Provision API

**Phase:** 1C (after PostgreSQL) + Phase 2 (Redis cache)
**Consumer:** Health module, Food module, Home Screen widget
**Source:** system-design.md §9.2

The main integration point between AnyMall-chan and other modules. Other modules do NOT own pet data — they request it from this endpoint.

**Path parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `pet_id` | string | Unique pet identifier |

**Response (200):**
```json
{
  "pet_id": "luna-001",
  "profile_summary": "CURRENT STATE:\nLuna is a 2-year-old female Shiba Inu on a raw food diet. Neutered. Currently on antibiotics for ear infection. Moderate energy level. No known chronic illness.\n\nHEALTH HISTORY:\nMar 2025: Ear infection, vet prescribed antibiotics.\n\nGAPS: Exercise level, recent weight change, vaccination schedule unknown.",
  "confidence_score": 72,
  "last_updated": "2026-03-08T14:30:00Z",
  "life_stage": "adult",
  "high_priority_gaps": ["exercise_level", "recent_weight_change"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `pet_id` | string | Echo of path parameter |
| `profile_summary` | string | Natural language summary (~900 tokens). Optimized for LLM consumption. |
| `confidence_score` | int | 0-100 |
| `last_updated` | string | ISO 8601 — when profile was last updated by the Aggregator |
| `life_stage` | string | `"puppy"` / `"junior"` / `"adult"` / `"senior"` |
| `high_priority_gaps` | list[string] | Fields we don't know yet that are important |

**Caching (Phase 2):**
- Redis key: `pet:{id}:profile_summary`
- TTL: 24 hours
- Rebuilt: current state section after every Aggregator update (template, no LLM). Historical section rebuilt by nightly batch job (LLM).

**Error responses:**
| Status | Condition |
|--------|-----------|
| 404 | Pet not found |
| 401 | Not authenticated (Phase 4) |

---

### 8. POST /api/v1/auth/login

**Phase:** 4
**Consumer:** Flutter app

**Request:**
```json
{
  "email": "user@example.com",
  "password": "..."
}
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

### 9. POST /api/v1/auth/refresh

**Phase:** 4
**Consumer:** Flutter app

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "expires_in": 3600
}
```

---

### 10. GET /api/v1/pet/{pet_id}/profile

**Phase:** 1C
**Consumer:** Flutter app (profile screen, home screen widget)

Returns the structured active profile for display in the app.

**Response (200):**
```json
{
  "pet_id": "luna-001",
  "name": "Luna",
  "species": "dog",
  "breed": "Shiba Inu",
  "life_stage": "adult",
  "confidence_score": 72,
  "confidence_color": "yellow",
  "fields": {
    "weight": { "value": "4 kg", "confidence": 0.75 },
    "diet_type": { "value": "raw food", "confidence": 0.80 },
    "allergies": null,
    "exercise_level": null
  },
  "gap_count": 8,
  "last_updated": "2026-03-08T14:30:00Z"
}
```

**Difference from `/debug/profile`:** This is the production-facing version with identity fields merged in and null for unknown fields. `/debug/profile` is raw Aggregator output for development.

---

### 11. PUT /api/v1/pet/{pet_id}/profile

**Phase:** 1C
**Consumer:** Flutter app (onboarding edit, manual correction)

Allows users to manually update pet profile fields. These updates use `source_rank: "user_correction"` which always wins in the Aggregator (Rule 2).

**Request:**
```json
{
  "fields": {
    "weight": "4.5 kg",
    "allergies": "chicken"
  }
}
```

**Response (200):**
```json
{
  "status": "ok",
  "updated_fields": ["weight", "allergies"]
}
```

---

### 12. GET /api/v1/pet/{pet_id}/facts

**Phase:** 1C
**Consumer:** Flutter app (fact history / transparency screen)

Production version of `/debug/facts`. Shows extracted facts for a specific pet with pagination.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 20 | Max entries |
| `offset` | int | 0 | Pagination offset |
| `key` | string | — | Filter by field name (e.g., `?key=weight`) |

**Response (200):**
```json
{
  "pet_id": "luna-001",
  "total": 45,
  "limit": 20,
  "offset": 0,
  "facts": [ ... ]
}
```

---

### 13. POST /api/v1/pet

**Phase:** 1C
**Consumer:** Flutter app (onboarding flow)

Creates a new pet profile during the onboarding process.

**Request:**
```json
{
  "name": "Luna",
  "species": "dog",
  "breed": "Shiba Inu",
  "date_of_birth": "2024-01-15",
  "sex": "female"
}
```

**Response (201):**
```json
{
  "pet_id": "luna-001",
  "name": "Luna",
  "life_stage": "adult",
  "confidence_score": 15,
  "confidence_color": "red"
}
```

---

### 14. GET /api/v1/sessions/{session_id}

**Phase:** 2
**Consumer:** Flutter app (conversation history)

Returns the message history for a specific session.

**Response (200):**
```json
{
  "session_id": "session-abc123",
  "message_count": 12,
  "created_at": "2026-03-08T10:00:00Z",
  "messages": [
    { "role": "user", "content": "Hi, how's Luna?", "timestamp": "..." },
    { "role": "assistant", "content": "Hi Shara! Luna...", "timestamp": "..." }
  ]
}
```

---

## Internal Interfaces (Not HTTP Endpoints)

These are internal function calls between agents — not exposed as HTTP routes.
Listed here for completeness since the system-design.md references them.

### Compressor.run()

```python
async def run(state: AgentState) -> list[ExtractedFact]
```

Called by `_run_background()` in `chat.py`. The `is_entity` gate skips the LLM call entirely if Agent 1 says no facts are present.

### Aggregator.run()

```python
async def run(facts: list[ExtractedFact], session_id: str) -> None
```

Called by `_run_background()` after Compressor. Pure deterministic logic — no LLM. Applies Rules 0-6 and writes to `active_profile`.

### IntentClassifier.classify()

```python
async def classify(message: str) -> tuple[str, str]
```

Returns `(intent_type, urgency)`. Called before Agent 1 on every request.

### ConversationAgent.run()

```python
async def run(
    user_message: str,
    session_messages: list,
    active_profile: dict,
    gap_list: list[str],
    pet_summary: str,
    pet_history: str,
    relationship_context: str,
    intent_type: str,
    questions_asked_so_far: int,
) -> AgentResponse
```

Returns `AgentResponse(message: str, is_entity: bool, questions_asked_count: int)`.

---

## Deep Link Schema (Mobile App Navigation)

Not HTTP endpoints — URL schemes the Flutter app navigates to when `redirect` is present in the `/chat` response.

| Deep Link | Target | Example |
|-----------|--------|---------|
| `anymall://health?query={text}&source=anymall_chan` | Health module chat | User reported vomiting |
| `anymall://food?query={text}&source=anymall_chan` | Food module chat | User asked about diet |

**Current Phase 1 simulators:** `/health/chat` and `/food/chat` render HTML pages. In production, the Flutter app will handle these deep links natively.

**Payload data passed via deep link:**

| Param | Description |
|-------|-------------|
| `query` | URL-encoded user message (pre-populates input field) |
| `source` | `"anymall_chan"` — for analytics tracking |
| `urgency` | `"high"` / `"medium"` / `"low"` — controls UI styling |
| `pet_id` | Pet identifier — target module calls Context API to load profile |

---

## Versioning Strategy

- Current endpoints (`/chat`, `/health`, `/debug/*`) have no version prefix — they are internal/dev endpoints.
- Production endpoints use `/api/v1/` prefix.
- When breaking changes are needed: introduce `/api/v2/` while keeping `/api/v1/` alive for backwards compatibility.
- The Flutter app will always target a specific API version.

---

## Authentication Flow (Phase 4)

```
Flutter app                           Backend
    │                                    │
    ├── POST /api/v1/auth/login ────────>│
    │   {email, password}                │
    │<── {access_token, refresh_token} ──│
    │                                    │
    ├── POST /chat ──────────────────────>│
    │   Authorization: Bearer {token}    │
    │<── ChatResponse ───────────────────│
    │                                    │
    ├── (token expires after 1 hour)     │
    │                                    │
    ├── POST /api/v1/auth/refresh ──────>│
    │   {refresh_token}                  │
    │<── {new_access_token} ─────────────│
```

All endpoints except `/health` and `/api/v1/auth/*` will require a valid JWT token.

---

## Rate Limiting (Phase 4)

| Endpoint | Limit | Key |
|----------|-------|-----|
| `POST /chat` | 20/minute | User ID (or IP if no auth) |
| `GET /api/v1/pet/*/context` | 60/minute | Module API key |
| `POST /api/v1/auth/login` | 5/minute | IP address |
| All other endpoints | 100/minute | User ID |

Implementation: `slowapi` middleware. After auth is added, rate limit by user ID instead of IP.
