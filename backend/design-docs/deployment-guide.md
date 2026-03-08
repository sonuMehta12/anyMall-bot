# AnyMall-chan Backend — Deployment & Review Guide

Last updated: 2026-03-08 | Phase 1B complete

This document is for team members who want to deploy, test, and review the backend.
Written for someone who has never seen the codebase.

---

## 1. What You're Deploying

A **pet companion chat API** powered by a multi-agent LLM pipeline.
The backend is a Python FastAPI server that talks to Azure OpenAI.

**What works right now:**

| Feature | Status | Description |
|---|---|---|
| Chat endpoint | Working | `POST /chat` — send a message, get an AI reply about your pet |
| Intent classification | Working | Automatically detects health/food concerns before responding |
| Health/food redirect | Working | Returns deeplink payload so mobile app can route to specialist modules |
| Fact extraction | Working | Agent 2 (Compressor) extracts pet facts from conversation in background |
| Fact aggregation | Working | Agent 3 (Aggregator) merges facts into a living pet profile |
| Confidence bar | Working | 0-100 score showing how well the system knows the pet |
| Guardrails | Working | Strips blocked jargon and preachy phrases from AI replies |
| Debug endpoints | Working | Inspect extracted facts and active profile via API |
| Health check | Working | `GET /health` — liveness check with LLM reachability |

**What is NOT built yet (intentionally deferred):**

| Missing | Why | When |
|---|---|---|
| Database | Using JSON files on disk — logic first, infrastructure later | Phase 1C |
| Redis sessions | Session history is in-memory, resets on restart | Phase 2 |
| Authentication | No JWT, no user identity — anyone with the URL can call the API | Phase 4 |
| Rate limiting | No request throttling — can be spammed | Phase 4 |
| CORS lockdown | `allow_origins=["*"]` — wide open for development | Phase 4 |
| Multi-pet support | Hardcoded to one pet (Luna) and one owner (Shara) | Phase 1C |

**This deployment is for internal review only. Do not expose to the public internet.**

---

## 2. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 or 3.13 recommended |
| pip | Latest | Comes with Python |
| Azure OpenAI access | — | Need endpoint URL, API key, and a `gpt-4.1` deployment |
| Git | Any | To clone the repo |

No Docker, no PostgreSQL, no Redis needed for this deployment.
The backend runs as a single process with zero external dependencies beyond Azure OpenAI.

---

## 3. Local Setup (Any Machine)

```bash
# 1. Clone the repo
git clone <repo-url>
cd AnyMall-chat/backend

# 2. Create virtual environment
python -m venv .venv

# Activate — pick your OS:
# Linux/Mac:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (Git Bash):
source .venv/Scripts/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env — fill in your Azure OpenAI credentials (see section below)

# 5. Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Server is ready when you see:
# "Starting up AnyMall-chan backend..."
# "Backend ready. LLM provider: azure"
```

### Environment Variables (.env)

```env
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_API_KEY=your-api-key-here
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_DEPLOYMENT_CHAT=gpt-4.1
```

Get these from the Azure Portal → your OpenAI resource → Keys and Endpoint.
The deployment name (`gpt-4.1`) must match exactly what's configured in Azure.

### First Run Behavior

On the first request, the server auto-creates `data/` directory with 4 JSON files:
- `pet_profile.json` — Luna (Shiba Inu) defaults
- `active_profile.json` — 5 seed facts (diet, vaccinations, etc.)
- `user_profile.json` — Shara (owner) defaults
- `fact_log.json` — empty, populated as you chat

These files are the "database" for now. Delete the `data/` folder to reset everything.

---

## 4. Quick Verification

After the server starts, run these commands to verify everything works:

```bash
# 1. Health check — should return {"status": "ok", "llm_reachable": true}
curl http://localhost:8000/health

# 2. Send a chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, Luna seems a bit tired today", "session_id": "test-1"}'

# 3. Wait 5-8 seconds (background agents run after reply), then check extracted facts
curl "http://localhost:8000/debug/facts?session_id=test-1"

# 4. Check the pet profile (Aggregator output)
curl http://localhost:8000/debug/profile

# 5. Send a health concern — should include redirect payload
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Luna has been vomiting since morning", "session_id": "test-2"}'
```

### Using the React Test UI

The `frontend/` folder has a React chat UI for visual testing.

```bash
# Terminal 2 (keep the backend running in Terminal 1)
cd frontend
npm install    # first time only
npm run dev    # starts on http://localhost:5173
```

Open `http://localhost:5173` in Chrome. Open DevTools → Console to see agent debug logs:
- **[Agent 1]** — logged immediately (reply, intent, urgency, is_entity)
- **[Agent 2]** — logged ~8s later (extracted facts with confidence)
- **[Agent 3]** — logged ~8s later (active profile state)

---

## 5. API Reference

### POST /chat

The core endpoint. Send a message, get a reply.

**Request:**
```json
{
  "message": "Luna weighs about 4kg",
  "session_id": "any-unique-string"
}
```

**Response:**
```json
{
  "message": "That's a healthy weight for a Shiba Inu! ...",
  "redirect": null,
  "session_id": "any-unique-string",
  "questions_asked_count": 0,
  "was_guardrailed": false,
  "is_entity": true,
  "intent_type": "general",
  "urgency": "low",
  "confidence_score": 42,
  "confidence_color": "red"
}
```

**Key fields:**
| Field | Type | Description |
|---|---|---|
| `message` | string | Agent 1's reply to the user |
| `redirect` | object/null | Present only for health/food intents (see below) |
| `session_id` | string | Echo of the session ID — use the same ID for conversation continuity |
| `is_entity` | bool | Did Agent 1 detect extractable pet facts in the message? |
| `intent_type` | string | `"general"` / `"health"` / `"food"` |
| `urgency` | string | `"low"` / `"medium"` / `"high"` |
| `confidence_score` | int | 0-100 — how well the system knows the pet |
| `confidence_color` | string | `"green"` (80-100) / `"yellow"` (50-79) / `"red"` (0-49) |

**Redirect payload (when intent is health or food):**
```json
{
  "redirect": {
    "module": "health",
    "deep_link": "http://localhost:8000/health/chat?query=...",
    "pre_populated_query": "Luna has been vomiting since morning",
    "pet_summary": "Luna is a 2-year-old female Shiba Inu...",
    "urgency": "high"
  }
}
```

### GET /health

Liveness check. Returns LLM reachability status.

```json
{
  "status": "ok",
  "llm_provider": "azure",
  "llm_reachable": true,
  "phase": "0"
}
```

### GET /debug/facts

Compressor (Agent 2) output. Shows extracted facts from conversations.

**Query params:**
- `session_id` (optional) — filter to one session
- `limit` (optional, default 20, max 100) — number of entries

```json
{
  "count": 1,
  "session_id_filter": "test-1",
  "facts": [
    {
      "key": "weight",
      "value": "4 kg",
      "confidence": 0.75,
      "source_rank": "explicit_owner",
      "time_scope": "current",
      "source_quote": "Luna weighs about 4kg",
      "needs_clarification": false,
      "session_id": "test-1",
      "extracted_at": "2026-03-08T14:30:00Z"
    }
  ]
}
```

### GET /debug/profile

Aggregator (Agent 3) output. Shows the current best-known facts about the pet.

```json
{
  "status": "ok",
  "field_count": 6,
  "profile": {
    "weight": {
      "value": "4 kg",
      "confidence": 0.75,
      "source_rank": "explicit_owner",
      "status": "new",
      "updated_at": "2026-03-08T14:30:00Z"
    }
  }
}
```

### GET /health/chat and GET /food/chat

Phase 1 simulator pages. These render HTML showing what a real Health/Food module
would receive. Used for testing redirect logic — not part of the production API.

---

## 6. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    POST /chat                           │
│                                                         │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ IntentClassifier  │───>│    Agent 1       │          │
│  │ (LLM, temp=0.0)  │    │ (LLM, temp=0.7)  │          │
│  │                   │    │                   │          │
│  │ Returns:          │    │ Returns:          │          │
│  │ - intent_type     │    │ - reply           │          │
│  │ - urgency         │    │ - is_entity       │          │
│  └──────────────────┘    └────────┬──────────┘          │
│                                    │                     │
│                          ┌────────v──────────┐          │
│                          │  Guardrails       │          │
│                          │  + Deeplink       │          │
│                          │  + Confidence     │          │
│                          └────────┬──────────┘          │
│                                    │                     │
│                          Reply sent to user              │
│                                    │                     │
│                    ┌───────────────v───────────────┐     │
│                    │  Background (fire-and-forget)  │    │
│                    │                                │    │
│                    │  ┌──────────────────┐          │    │
│                    │  │  Compressor      │          │    │
│                    │  │  (LLM, temp=0.0) │          │    │
│                    │  │  → fact_log.json │          │    │
│                    │  └────────┬─────────┘          │    │
│                    │           │                     │    │
│                    │  ┌────────v─────────┐          │    │
│                    │  │  Aggregator      │          │    │
│                    │  │  (no LLM)        │          │    │
│                    │  │  → active_profile│          │    │
│                    │  └──────────────────┘          │    │
│                    └────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

**LLM calls per request:** 2 (IntentClassifier + Agent 1) + 0-1 background (Compressor).
Aggregator is pure logic — no LLM. Total: 2-3 Azure OpenAI calls per message.

**Latency:** User-facing response typically 2-4 seconds (two LLM calls).
Background pipeline runs after the response is sent — user never waits for it.

---

## 7. File Structure

```
backend/
├── app/
│   ├── main.py                      # App creation, CORS, lifespan, /health
│   ├── agents/
│   │   ├── conversation.py          # Agent 1 — conversational AI
│   │   ├── intent_classifier.py     # Classifies health/food/general + urgency
│   │   ├── compressor.py            # Agent 2 — extracts facts from conversation
│   │   ├── aggregator.py            # Agent 3 — merges facts into profile
│   │   └── state.py                 # AgentState dataclass (pipeline context)
│   ├── routes/
│   │   ├── chat.py                  # POST /chat + background pipeline
│   │   ├── debug.py                 # GET /debug/facts, /debug/profile
│   │   └── simulator.py            # Health/food module simulators
│   ├── services/
│   │   ├── guardrails.py            # Response content filtering
│   │   ├── deeplink.py              # Redirect payload builder
│   │   ├── context_builder.py       # Reads JSON files → 5 context values
│   │   └── confidence_calculator.py # Confidence score computation
│   ├── storage/
│   │   └── file_store.py            # JSON file read/write helpers
│   ├── models/
│   │   └── context.py               # PetProfile, ActiveProfileEntry, UserProfile
│   ├── llm/
│   │   ├── base.py                  # Abstract LLMProvider interface
│   │   ├── azure_openai.py          # Azure OpenAI implementation
│   │   └── factory.py               # Provider factory
│   └── core/
│       └── config.py                # Environment config (pydantic-settings)
├── constants.py                     # Business logic constants
├── requirements.txt                 # Python dependencies (6 packages)
├── .env.example                     # Environment variable template
├── tests/
│   └── run_e2e.py                   # 24 automated end-to-end tests
├── design-docs/                     # Architecture and design documents
│   ├── deployment-guide.md          # This file
│   ├── system-design.md             # Full system architecture
│   ├── compressor-design.md         # Agent 2 design + decision log
│   ├── aggregator-design.md         # Agent 3 design + conflict rules
│   ├── security.md                  # Known risks + fix schedule
│   └── confidence-bar.md            # Confidence score design
└── data/                            # Runtime data (gitignored, auto-created)
    ├── fact_log.json                # Extracted facts log (append-only)
    ├── pet_profile.json             # Static pet identity
    ├── active_profile.json          # Dynamic pet profile
    └── user_profile.json            # Owner relationship data
```

---

## 8. Cloud Deployment Options

The backend is a standard ASGI Python app. It runs anywhere that supports Python 3.11+.

### Option A — Azure App Service (recommended if already using Azure OpenAI)

```bash
# 1. Install Azure CLI
az login

# 2. Create resource group + app service plan
az group create --name anymall-rg --location eastus
az appservice plan create --name anymall-plan --resource-group anymall-rg \
  --sku B1 --is-linux

# 3. Create web app
az webapp create --name anymall-chat-api --resource-group anymall-rg \
  --plan anymall-plan --runtime "PYTHON:3.12"

# 4. Set environment variables
az webapp config appsettings set --name anymall-chat-api \
  --resource-group anymall-rg --settings \
  LLM_PROVIDER=azure \
  AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com \
  AZURE_OPENAI_API_KEY=your-key \
  AZURE_OPENAI_API_VERSION=2025-01-01-preview \
  AZURE_OPENAI_DEPLOYMENT_CHAT=gpt-4.1

# 5. Set startup command
az webapp config set --name anymall-chat-api --resource-group anymall-rg \
  --startup-file "uvicorn app.main:app --host 0.0.0.0 --port 8000"

# 6. Deploy from local
cd backend
az webapp up --name anymall-chat-api --resource-group anymall-rg
```

**Cost estimate:** Azure App Service B1 (~$13/month) + Azure OpenAI usage (~$5-20/month
for internal testing). Total: ~$20-35/month.

### Option B — Railway

```bash
# 1. Install Railway CLI
npm install -g @railway/cli
railway login

# 2. Initialize project
cd backend
railway init

# 3. Set environment variables
railway variables set LLM_PROVIDER=azure
railway variables set AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
railway variables set AZURE_OPENAI_API_KEY=your-key
railway variables set AZURE_OPENAI_API_VERSION=2025-01-01-preview
railway variables set AZURE_OPENAI_DEPLOYMENT_CHAT=gpt-4.1

# 4. Add Procfile
echo "web: uvicorn app.main:app --host 0.0.0.0 --port \$PORT" > Procfile

# 5. Deploy
railway up
```

### Option C — Render

1. Push `backend/` to a GitHub repo
2. Go to render.com → New Web Service
3. Connect your repo, set root directory to `backend`
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Add environment variables in the Render dashboard

### Option D — Any Linux VM (EC2, DigitalOcean, etc.)

```bash
# SSH into your server
ssh your-server

# Clone and setup
git clone <repo-url>
cd AnyMall-chat/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create .env
cp .env.example .env
nano .env  # fill in credentials

# Run with auto-restart
pip install gunicorn
gunicorn app.main:app -w 1 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 --timeout 120

# For production: use systemd service or supervisor to keep it running
```

**Important:** Use `-w 1` (single worker). The current storage uses JSON files on disk —
multiple workers would cause write conflicts. Phase 1C (PostgreSQL) removes this limitation.

---

## 9. Deployment Checklist

Before deploying, verify these:

- [ ] `.env` has valid Azure OpenAI credentials
- [ ] `GET /health` returns `{"llm_reachable": true}`
- [ ] `POST /chat` returns a real AI response
- [ ] `GET /debug/facts` shows extracted facts after a few messages
- [ ] `GET /debug/profile` shows the active profile
- [ ] `data/` directory is writable by the server process
- [ ] Server runs with **single worker** only (`-w 1`)

### Running the Test Suite

```bash
# Make sure the server is running on port 8000 first
cd backend
python tests/run_e2e.py
```

Runs 24 tests across 5 sections: infrastructure, intent routing, session management,
Compressor fact extraction, Aggregator profile merging.

**Expected result:** 21-24 passing. 2-3 tests may intermittently fail due to LLM
non-determinism (Agent 1 sometimes classifies borderline messages differently).
This is a known limitation of LLM-dependent tests, not a code bug.

---

## 10. What to Review

For team members reviewing the backend, here's what to focus on:

### Functional Review (try these scenarios)

1. **Basic conversation** — Send 3-4 messages about Luna. Check that replies are
   contextual and remember earlier messages in the same session.

2. **Fact extraction** — Tell the system "Luna weighs 4kg" or "Luna is allergic to chicken".
   Wait 8 seconds. Check `/debug/facts` — facts should appear with confidence scores.
   Check `/debug/profile` — high-confidence facts should be merged.

3. **Health redirect** — Say "Luna has been vomiting blood". Response should be short
   (empathy only, no advice) with a `redirect` payload pointing to the health module.

4. **Urgency levels** — "Luna seems a bit tired" (low) vs "Luna is having a seizure" (high).
   Check that `urgency` in the response matches.

5. **Fact conflict resolution** — Tell the system "Luna weighs 4kg", then later
   "actually Luna weighs 4.5kg". The profile should update (Rule 2: user correction).

6. **Confidence bar** — Watch `confidence_score` increase as you provide more facts
   across messages. Starts low (red), grows toward green as the system learns more.

7. **New session** — Use a different `session_id`. Conversation history resets but
   the pet profile persists (facts carry over between sessions via JSON files).

### Architecture Review

- **Agent isolation** — Agents never import concrete LLM providers. Check `conversation.py`,
  `intent_classifier.py`, `compressor.py` — they all use `self._llm.complete()`.

- **Storage abstraction** — `file_store.py` is the only file that touches disk.
  Agents don't know if storage is JSON or PostgreSQL.

- **Background pipeline** — `_run_background()` in `chat.py` runs via `asyncio.create_task()`.
  User-facing response is returned before Compressor/Aggregator run.

- **Prompt engineering** — Read the system prompts in `conversation.py` and
  `compressor.py`. These are where most of the intelligence lives.

### Known Gaps to Flag

- **Single pet, single owner** — No multi-tenancy. Every session talks about Luna.
- **No auth** — API is wide open. Fine for internal review, must fix before production.
- **In-memory sessions** — Server restart loses conversation history (not pet profile).
- **JSON file storage** — Works for one server instance. Won't scale to multiple workers.
- **Rate limiting** — None. A loop could rack up Azure costs fast.

---

## 11. Security Notes

**This build is for internal review only.**

| Risk | Severity | Current State | Fix Phase |
|---|---|---|---|
| No authentication | High | Wide open | Phase 4 |
| No rate limiting | High | Unlimited requests | Phase 4 |
| CORS `allow_origins=["*"]` | Medium | Any origin can call | Phase 4 |
| Settings `__repr__` leaks API key in logs | Medium | Key visible in log output | Phase 4 |
| In-memory sessions grow unbounded | High | OOM risk with many users | Phase 2 |
| `/health` makes real LLM call each ping | Low | Wastes tokens | Phase 2 |

Full details: `design-docs/security.md`

**Do not expose this server to the public internet.**
For review, use:
- `localhost` access only, or
- VPN/private network, or
- IP-restricted cloud deployment (security groups, firewall rules)

---

## 12. Cost Estimate (Azure OpenAI)

Each chat message makes 2-3 LLM calls:

| Call | Model | Input tokens (est.) | Output tokens (est.) |
|---|---|---|---|
| IntentClassifier | gpt-4.1 | ~200 | ~20 |
| Agent 1 | gpt-4.1 | ~800 | ~150 |
| Compressor (if facts detected) | gpt-4.1 | ~600 | ~200 |

**Per message:** ~1,600-1,800 input tokens + ~170-370 output tokens.

At GPT-4.1 pricing (~$2/1M input, ~$8/1M output):
- **Per message:** ~$0.003-0.006
- **100 messages/day (light testing):** ~$0.30-0.60/day
- **1,000 messages/day (heavy testing):** ~$3-6/day

This is well within a typical Azure OpenAI budget for development.

---

## 13. Troubleshooting

### Server starts but no logs, old responses
Another process is holding port 8000. Find and kill it:
```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <pid> /F

# Linux/Mac
lsof -i :8000
kill -9 <pid>
```

### "Agent not initialised yet" (503)
Request arrived before lifespan finished. Wait a second and retry.
If persistent, check that Azure credentials in `.env` are correct.

### Compressor returns no facts
Check `is_entity` in the chat response. If `false`, Agent 1 decided the message
contained no extractable pet facts — Compressor skips entirely (by design).
Try a message with a clear fact: "Luna weighs 4kg" or "Luna is 2 years old".

### Facts don't appear in /debug/facts
The background pipeline runs after the response is sent. Wait 5-8 seconds.
If still empty, check server logs for "Background pipeline failed" errors.

### data/ folder not created
The server needs write permission to the `backend/` directory.
On Linux: `chmod 755 backend/` or run as a user with write access.

### LLM returns unexpected format
Occasionally the LLM returns malformed JSON. The code handles this with retry
logic (IntentClassifier) or graceful fallback (Compressor). If it happens
repeatedly, check that the deployment name in `.env` matches your Azure setup.

---

## 14. Resetting State

```bash
# Reset everything — delete the data folder
rm -rf data/

# Next request will re-seed Luna + Shara defaults automatically
```

To reset just the learned facts (keep seed data):
```bash
rm data/fact_log.json data/active_profile.json
# These will be recreated with defaults on next request
```

---

## 15. Next Steps (Roadmap)

| Phase | What | Status |
|---|---|---|
| Phase 1C | PostgreSQL replaces JSON files | Next up |
| Phase 2 | Redis for sessions + cache | Planned |
| Phase 3 | Session compaction, nightly batch jobs | Planned |
| Phase 4 | JWT auth, rate limiting, CORS lockdown | Planned |
| Phase 5 | Unit tests, integration tests, Docker, production deploy | Planned |

The backend is designed so that PostgreSQL and Redis plug in without changing
any agent code. The agents talk to `file_store.py` (storage layer) and
`context_builder.py` (context layer) — only those two files change in Phase 1C.
