# AnyMall-chan — Pet Companion Chat AI

A three-agent pet health chat system powered by LLM. Understands pet context, extracts facts from conversation, and builds a living profile over time.

## Quick Start

```bash
# 1. Install backend dependencies
cd backend
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env — fill in your Azure OpenAI credentials

# 3. Start the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Start the React test UI (separate terminal)
cd frontend
npm install
npm run dev
# Opens on http://localhost:5173
```

## How It Works

```
User message
    → IntentClassifier (LLM)       health / food / general + urgency
    → Agent 1: Conversation (LLM)  empathetic bilingual response
    → Guardrails                   tone + safety checks
    → Deeplink Builder             redirect payload for health/food intents
    → Confidence Calculator        score + color (coverage × recency × depth)
    → Response to user
    ↓  [fire-and-forget — user does NOT wait]
    → Agent 2: Compressor (LLM)    extract facts → fact_log.json
    → Agent 3: Aggregator (rules)  merge facts → active_profile.json
```

## Try These Messages

```
"Luna seems tired today"           → extracts energy_level
"She eats twice a day"             → extracts feeding_frequency (fills a gap!)
"She ate raw food this morning"    → confirms existing diet_type (boosts confidence)
"Actually she eats kibble"         → user_correction → wins over existing value
"Luna is vomiting since morning"   → medical intent → redirect card
"What food is best for her"        → nutritional intent → redirect card
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Send a message — runs full pipeline |
| GET | `/health` | Health check |
| GET | `/debug/facts` | View extracted facts log |
| GET | `/debug/profile` | View active pet profile |
| GET | `/health/chat` | Health module simulator |
| GET | `/food/chat` | Food module simulator |

Interactive docs: http://localhost:8000/docs

## File Structure

```
AnyMall-chat/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app, CORS, lifespan, /health
│   │   ├── agents/
│   │   │   ├── intent_classifier.py   # LLM-based intent classification
│   │   │   ├── conversation.py        # Agent 1 — bilingual conversation
│   │   │   ├── compressor.py          # Agent 2 — fact extraction (LLM)
│   │   │   ├── aggregator.py          # Agent 3 — fact merge (rules, no LLM)
│   │   │   └── state.py              # AgentState dataclass
│   │   ├── routes/
│   │   │   ├── chat.py               # POST /chat + background pipeline
│   │   │   ├── debug.py              # Debug endpoints
│   │   │   └── simulator.py          # Health/food simulator pages
│   │   ├── services/
│   │   │   ├── guardrails.py         # Safety + tone checks
│   │   │   ├── deeplink.py           # Redirect payload builder
│   │   │   ├── context_builder.py    # Reads pet context from data files
│   │   │   └── confidence_calculator.py
│   │   ├── storage/
│   │   │   └── file_store.py         # JSON file read/write
│   │   ├── models/
│   │   │   └── context.py            # PetProfile, ActiveProfileEntry, UserProfile
│   │   ├── llm/
│   │   │   ├── base.py              # Abstract LLMProvider
│   │   │   ├── azure_openai.py      # Azure OpenAI implementation
│   │   │   └── factory.py           # Provider factory
│   │   └── core/
│   │       └── config.py            # .env → Settings
│   ├── constants.py                  # Business logic constants
│   ├── requirements.txt
│   ├── tests/
│   │   └── run_e2e.py               # 24 end-to-end tests
│   └── data/                         # gitignored — created at runtime
│       ├── fact_log.json
│       ├── pet_profile.json
│       ├── active_profile.json
│       └── user_profile.json
├── frontend/                          # React + Vite test UI
│   ├── src/
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## What's Next

- **Phase 1C:** Swap JSON files for PostgreSQL
- **Phase 2:** Redis cache for active profiles
- **Phase 3:** Session compaction, nightly batch jobs
- **Phase 4:** JWT auth + rate limiting
- **Phase 5:** Tests + production deployment
