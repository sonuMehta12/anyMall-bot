# Pet Health Companion — Prototype

A working prototype of the three-agent pet health chat system.
No real LLM, no real database — everything runs in memory with mock responses.

## Quick Start

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Run the API server (from the project root)
cd ..
uvicorn backend.main:app --reload --port 8000

# 3. Open the frontend
# Simply open frontend/index.html in your browser
# Or serve it: python -m http.server 3000 --directory frontend
```

Then open `frontend/index.html` in your browser.

---

## What This Demonstrates

| Feature | How to See It |
|---------|--------------|
| Three-agent pipeline | Send any message — watch the pipeline bar animate through 6 steps |
| Confidence bar | Sidebar shows Coverage/Recency/Depth scores updating live |
| Profile building | Send "she eats twice a day" → see profile sidebar update |
| Medical redirect | Send "Luna is vomiting" → redirect card appears, no medical advice given |
| Nutritional redirect | Send "what should she eat" → redirects to nutrition module |
| Conflict resolution | Tell it "she eats kibble" (Luna already has raw food) → aggregator handles it |
| Gap tracking | Red gap items show what's missing or stale |
| Passive batch | Click "Simulate Nightly Batch" → profile updates from mock health/food logs |
| Fact log (audit trail) | GET /debug/fact-log/luna-001 — see every fact ever extracted |

## Demo Pets

**Luna** (luna-001) — 2yo Shiba Inu
- Pre-filled: diet, medications, chronic illness, toilet timing, energy
- Missing: feeding frequency (Rank A gap), exercise (stale), weight change
- Confidence: ~65% Yellow

**Koko** (koko-001) — 5yo Persian Cat
- Only diet_type filled
- Confidence: ~15% Red — great demo of low-confidence state

## Try These Messages

```
"Luna seems tired today"           → extracts energy_level
"She eats twice a day"             → extracts feeding_frequency (fills a gap!)
"She ate raw food this morning"    → confirms existing diet_type (boosts confidence)
"Actually she eats kibble"         → user_correction → wins over existing raw food
"Luna is vomiting since morning"   → medical intent → redirect card
"What food is best for her"        → nutritional intent → redirect card
"She takes a 30 min walk daily"    → extracts exercise_level
"She's home alone for 8 hours"     → extracts home_alone_frequency
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/pets` | List demo pets with confidence scores |
| POST | `/session/start` | Start a new chat session |
| POST | `/chat` | Send a message — runs full pipeline |
| GET | `/pet/{id}/profile` | Full active profile + gaps + confidence |
| GET | `/pet/{id}/context` | NL summary for external modules |
| POST | `/batch/nightly` | Simulate passive context gathering |
| GET | `/debug/fact-log/{id}` | View append-only fact audit log |
| GET | `/debug/store-stats` | View in-memory store stats |

Interactive docs: http://localhost:8000/docs

---

## Architecture

```
User Message
    │
    ▼
[Guardrails Layer 1] ──── classify_intent() → "general" | "medical" | "nutritional"
    │
    ▼
[Context Builder] ──── active_profile (cache) + history + gaps + relationship context
    │
    ▼
[Agent 1: Conversation] ── MockLLMProvider → empathetic response (smart templates)
    │
    ▼
[Guardrails Layers 2+3] ── sanitize jargon + check tone + build redirect payload
    │
    ▼ (async)
[Agent 2: Compressor] ──── regex gate → mock fact extraction → [{key, value, confidence}]
    │
    ▼
[Agent 3: Aggregator] ──── deterministic conflict resolution → fact_log + active_profile
    │
    ▼
[Confidence Calculator] ── Coverage×0.4 + Recency×0.3 + Depth×0.3 → score + color
    │
    ▼
Response + confidence score → User
```

## Swapping to Real LLM / DB

All swap points are clean interfaces:

**Real LLM (Agent 1):**
```python
# In agents/conversation_agent.py
class AnthropicProvider(LLMConversationProvider):
    def __init__(self, model="claude-sonnet-4-6"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, context: dict) -> dict:
        # Build prompt from context, call API, parse JSON response
        ...

agent = ConversationAgent(provider=AnthropicProvider())
```

**Real LLM (Agent 2):**
```python
# In agents/compressor.py
class AnthropicExtractorProvider(LLMExtractorProvider):
    def extract(self, message: str, pet_id: str) -> list[dict]:
        # Call claude-haiku-4-5 with Compressor prompt template
        ...

compressor = Compressor(provider=AnthropicExtractorProvider())
```

**Real DB:**
```python
# Replace InMemoryStore with PostgresStore implementing the same methods:
# append_fact_log(), upsert_active_profile(), get_active_profile(), etc.
# Replace cache_get/cache_set with Redis calls.
```

## File Structure

```
/
├── backend/
│   ├── main.py                    # FastAPI app — all routes
│   ├── config.py                  # Constants (priority schema, decay tables, patterns)
│   ├── requirements.txt
│   ├── agents/
│   │   ├── conversation_agent.py  # Agent 1: LLMProvider interface + MockLLMProvider
│   │   ├── compressor.py          # Agent 2: regex gate + LLMExtractorProvider
│   │   └── aggregator.py          # Agent 3: deterministic conflict resolution
│   ├── data/
│   │   ├── store.py               # InMemoryStore (fact_log, active_profile, cache, ...)
│   │   └── seed_data.py           # Demo pets: Luna (Shiba Inu) + Koko (Persian Cat)
│   └── services/
│       ├── confidence_calculator.py  # Coverage × Recency × Depth formula
│       ├── context_builder.py        # build_agent_context() — assembles Agent 1 inputs
│       ├── guardrails.py             # 3-layer defense + redirect builder
│       └── gap_analyzer.py           # Missing/stale field detection
├── frontend/
│   └── index.html                 # Single-file vanilla JS chat UI
└── README.md
```
