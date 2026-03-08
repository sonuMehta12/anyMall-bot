# AnyMall-chan Backend — Security & Production Risks

This file tracks known security issues and production risks.
Each item has a severity, the phase it must be fixed in, and exactly what to do.

---

## Fixed in Phase 0 (already done)

| # | Issue | Fix Applied |
|---|---|---|
| S-01 | `import` inside function body in guardrails.py | Moved to top-level imports |
| S-02 | Duplicate `import sys` / `import os` in main.py | Consolidated at top |
| S-03 | Guardrail regex compiled on every request | Pre-compiled at module load |
| S-04 | `allow_credentials=True` with `allow_origins=["*"]` (CORS spec violation) | Changed to `allow_credentials=False` |

---

## Must Fix Before Production (Phase 4)

### S-05 — `Settings.__repr__` exposes API key in plain text
**Severity:** Medium
**Risk:** Any log line that prints the `settings` object will expose the Azure API key
in your logging service. Accessible to everyone with log access. Stored potentially forever.

**File:** `app/core/config.py`

**Fix:** Add a custom `__repr__` to the `Settings` class that masks sensitive fields:
```python
def __repr__(self) -> str:
    return (
        f"Settings("
        f"llm_provider={self.llm_provider!r}, "
        f"azure_openai_endpoint={self.azure_openai_endpoint!r}, "
        f"azure_openai_api_key='***masked***', "
        f"azure_openai_deployment_chat={self.azure_openai_deployment_chat!r}"
        f")"
    )
```

---

### S-06 — No rate limiting on POST /chat
**Severity:** High
**Risk:** Any user (or a bug in the Flutter app) can send unlimited requests per second.
Each request costs real money in Azure tokens. A simple abuse attack or infinite loop
could rack up hundreds of dollars in minutes before anyone notices.

**File:** `app/main.py`

**Fix (Phase 4):** Add `slowapi` rate limiting middleware:
```python
# pip install slowapi
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/chat")
@limiter.limit("20/minute")   # max 20 messages per IP per minute
async def chat(request: Request, body: ChatRequest): ...
```
When auth is added (Phase 4), switch `key_func` from IP address to user ID.

---

### S-07 — CORS `allow_origins=["*"]` must be locked down
**Severity:** Medium
**Risk:** Any website on the internet can make requests to the API from a user's browser.
In production this should be restricted to the specific Flutter web domain.

**File:** `app/main.py`

**Fix (Phase 4):**
```python
allow_origins=[
    "https://anymall.app",
    "https://www.anymall.app",
],
allow_credentials=True,   # now safe — specific origins, not wildcard
```

---

## Fix in Phase 2

### S-08 — `_sessions` dict grows unbounded (memory leak)
**Severity:** High
**Risk:** Every unique `session_id` adds an entry to `_sessions` that is never deleted.
With real users, this dict grows until the server runs out of RAM and crashes (OOM).

**File:** `app/main.py`
**Current code:** `_sessions: dict[str, list[dict[str, str]]] = {}`

**Fix (Phase 2):** Replace the dict with Redis. Redis supports TTL (Time To Live):
```python
# Sessions auto-expire after 2 hours of inactivity — no cleanup code needed
await redis.setex(f"session:{session_id}", 7200, json.dumps(messages))
```

---

### S-09 — `health_check()` makes a real Azure API call on every ping
**Severity:** Low
**Risk:** Load balancers and monitoring tools ping `/health` every 10–30 seconds.
Each ping makes a real Azure API call (5 tokens). At 30-second intervals = 2,880 calls/day
= ~86,000 calls/month, just for health checks. Costs money and adds 1–2 seconds latency.

**File:** `app/llm/azure_openai.py`

**Fix (Phase 2):** Cache the last health result with a 60-second TTL:
```python
import time

_last_health_check: tuple[float, bool] | None = None   # (timestamp, result)
HEALTH_CACHE_TTL = 60  # seconds

async def health_check(self) -> bool:
    global _last_health_check
    if _last_health_check and (time.time() - _last_health_check[0]) < HEALTH_CACHE_TTL:
        return _last_health_check[1]   # return cached result
    result = await self._do_health_check()
    _last_health_check = (time.time(), result)
    return result
```

---

## Fix in Phase 1

### S-10 — Question counting by `?` character is unreliable
**Severity:** Low
**Risk:** We count `?` in assistant messages to track how many gap questions were asked.
False positives: rhetorical phrases like "she's fine, right?" count as a question.
False negatives: Agent 1 could ask two questions in one message — we cap at 1, but the
second question still reaches the user.

**File:** `app/main.py` (heuristic counter), `app/agents/conversation.py` (the cap)

**Fix (Phase 1):** Track `questions_asked` as an explicit integer field stored in the
session record alongside the messages. Agent 1 returns it in `AgentResponse`.
No string counting, no heuristics.

---

## Permanent Rules (enforce forever)

- Never commit `.env`. API keys, endpoints → `.env` only, always gitignored.
- Never log the full `settings` object — it contains the API key.
- Never hardcode credentials, even in tests. Use environment variables or test fixtures.
- All secrets loaded exclusively through `app/core/config.py`.
- CORS origins must be explicit and minimal in production. `"*"` is development only.
