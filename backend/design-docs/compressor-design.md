# Agent 2 (Compressor) — Design Document

Plain thinking. All ideas, open questions, and decisions in one place.
Written before any code. Updated as decisions get made.

---

## 1. What the Compressor Does (One Paragraph)

The Compressor reads a single user message and extracts structured facts about the pet.
"Luna weighs about 4kg" becomes `{key: "weight_kg", value: "4", confidence: 0.75, ...}`.
It does not reply to the user. It does not call Agent 1. It runs in the background after
the response is already sent. The user never waits for it.

---

## 2. The Core Problem: Not Every Message Is Worth Processing

Most messages in a pet chat app are conversational noise:
- "ok thanks"
- "got it!"
- "haha yeah"
- "what should I do?"

Running an LLM on these is wasteful. We need a gate that decides:
**does this message likely contain extractable facts?**

This gate is what we're calling the `is_entity` flag.

**Core principle (non-negotiable):**
> We are comfortable processing messages that turn out to have no facts.
> We cannot afford to skip messages that do have facts.
> The gate must have zero false negatives. False positives are acceptable.

---

## 3. The `is_entity` Gate — Decision: Option C

### Chosen: Option C — ConversationAgent Outputs the Flag

Agent 1 already has the full picture: pet profile, conversation history, and the user's
message. It is the best-positioned node to know whether the user revealed a new fact.
We add one field to Agent 1's structured output: `"is_entity": true/false`.

If `is_entity` is true → run Compressor in background.
If `is_entity` is false → skip. No LLM call, no background task.

**Why this satisfies the core principle:**
Agent 1 understands context. It will not miss "she lost a bit of weight" the way regex
would not miss it either, but it also won't flag "ok thanks" like regex might.

**What this requires:**
Agent 1's response must become structured JSON (not plain text) so we can reliably
read the `is_entity` field. This is a change to Agent 1's output format — tracked
as a dependency below.

### Option A — Regex Pre-filter: Deferred

A cheap regex check before any LLM call. Fast and free, but dumb.
Regex cannot understand negation, past tense, or sarcasm. Deferred.

**Future hybrid use:** If we add regex later, it will only be used as soft HINTS passed
into the Compressor's prompt (not as a gate). Never use regex to skip a message.
The core principle above applies permanently.

### Option B — IntentClassifier Adds the Flag: Not Chosen

Adding a fourth output to IntentClassifier risks degrading its primary job.
Two different tasks in one tiny LLM call is a bad tradeoff.

---

## 4. Shared Agent State

A plain Python dataclass created at the start of each request. Every agent in the
pipeline receives it, reads what it needs, and writes what it produces.

### Structure

```python
@dataclass
class AgentState:
    # INPUT — set at request start, never modified
    session_id: str
    user_message: str
    pet_name: str
    pet_species: str
    pet_age: str
    pet_sex: str
    pet_weight: str

    # WRITTEN BY IntentClassifier
    intent_type: str = "general"       # "health" | "food" | "general"
    urgency: str = "none"              # "high" | "medium" | "low" | "none"
    is_entity: bool = False            # set by ConversationAgent (Option C)

    # WRITTEN BY ConversationAgent
    agent_reply: str = ""
    recent_history: list = field(default_factory=list)  # last 3 turns

    # WRITTEN BY Compressor (this turn only — replaced each turn, not accumulated)
    extracted_facts: list = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)

    # WRITTEN BY Aggregator
    profile_updated: bool = False
    fields_updated: list[str] = field(default_factory=list)
```

### `low_confidence_fields` — Last Turn Only

This list is **replaced every turn**, not accumulated.

It holds only the low-confidence fields found in the CURRENT turn's Compressor run.
ConversationAgent reads it on the NEXT turn and weaves a clarification question in.
If the user answers, the next Compressor run finds the same field with higher confidence
and the cycle closes naturally. If the user ignores it, the field simply disappears from
state on the next turn — no cleanup logic, no expiry timers, no stack management.

Simple. Predictable. No state leak across many turns.

---

## 5. Compressor Inputs — Final List

| Input | Source | Why |
|---|---|---|
| `user_message` | AgentState | The source of facts — always required |
| `recent_history` | AgentState | Last 3 turns. Needed when user says "yes" or "she does" without a noun — pronoun resolution. |
| `pet_name` | AgentState | So the LLM knows "she" = Luna |
| `pet_species` | AgentState | Cats vs dogs have different health fields that matter |
| `pet_age` | AgentState | "ate less today" means something different for a puppy vs a senior dog |
| `pet_sex` | AgentState | Pronoun disambiguation. Affects neutered/spayed field relevance. |
| `pet_weight` | AgentState | If user says "she lost weight", LLM needs the baseline to understand magnitude |

**Excluded:**
- Active profile / gap list — Compressor's job is to extract, not to decide what's missing
- Full conversation history — 3 turns is enough for pronoun resolution, full history is too expensive
- Regex hints — deferred entirely (see Section 3)

---

## 6. Structured Output — The Full Schema

```json
{
  "is_entity": true,
  "facts": [
    {
      "key": "energy_level",
      "value": "low/tired",
      "confidence": 0.75,
      "time_scope": "current",
      "uncertainty": "casual observation — may not persist",
      "source_quote": "seems tired today",
      "timestamp": null
    },
    {
      "key": "appetite",
      "value": "decreased — barely ate",
      "confidence": 0.80,
      "time_scope": "current",
      "uncertainty": "",
      "source_quote": "barely touched her kibble this morning",
      "timestamp": null
    },
    {
      "key": "diet_type",
      "value": "kibble",
      "confidence": 0.60,
      "time_scope": "current",
      "uncertainty": "not explicitly confirmed as primary diet — may eat other things too",
      "source_quote": "her kibble",
      "timestamp": null
    }
  ]
}
```

### Field Descriptions

| Field | Type | Description |
|---|---|---|
| `key` | string | Snake_case field name. Use preferred taxonomy when possible. |
| `value` | string | Always a string. Normalize units: "4 kilos" → value="4", key="weight_kg". |
| `confidence` | float | 0.0–1.0. LLM assigns based on language certainty. See scoring rules below. |
| `time_scope` | string | `"current"` (present state) \| `"past"` (resolved/historical) \| `"unknown"` |
| `uncertainty` | string | Plain text reason WHY confidence is what it is. Empty string if confident. |
| `source_quote` | string | The exact substring from the message that supports this fact. |
| `timestamp` | string \| null | ISO timestamp only when the user explicitly states a time ("vet said last Tuesday"). Otherwise null. |

### Why `time_scope` Matters

Without it, "Luna had pneumonia" and "Luna has pneumonia" are identical records.
The Aggregator cannot know which field to write to.

With it:
- `time_scope="past"` → write to `past_conditions`, never overwrite `current_conditions`
- `time_scope="current"` → write to `current_conditions`
- `time_scope="unknown"` → write with reduced confidence, flag for review

### Why `uncertainty` Matters

A confidence number alone (0.60) tells us nothing useful. "not explicitly confirmed as
primary diet — may eat other things too" tells us exactly what Agent 1 needs to phrase
a natural clarification question. It also gives us a readable audit trail in fact_log.

### Preferred Key Taxonomy

Give the LLM these names to use when applicable. Free-form snake_case allowed for anything else.

```
breed, age_years, weight_kg, sex, neutered,
diet_type, food_brand, allergies,
current_conditions, past_conditions,
current_medications, past_medications,
vaccination_status, vet_name, vet_clinic,
energy_level, temperament, behavioral_traits,
appetite, activity_level
```

---

## 7. Confidence Handling

### Threshold

| Confidence | Action |
|---|---|
| > 0.70 | Write to fact_log. Aggregator processes and updates active_profile. |
| 0.50–0.70 | Write to fact_log with `needs_clarification: true`. Do NOT update active_profile. Add key to `state.low_confidence_fields`. |
| < 0.50 | Discard. Too uncertain to be useful. |

### Confidence Scoring Rules (In the Prompt)

```
0.95 — hard specific fact with specificity ("vet confirmed exactly 4.2kg")
0.85 — stated confidently, no hedging ("Luna weighs 4kg")
0.75 — mild hedging ("about 4kg", "roughly", "around")
0.60 — clear uncertainty ("I think", "maybe", "probably")
0.50 — speculative or second-hand ("I heard", "someone told me", "could be")
```

Additional rules:
- If user uses pet's name directly → +0.05 (clear subject)
- If source is a vet → override `source_rank` to "vet_record" → +0.10
- If negation ("no allergies") → `value="none confirmed"`, confidence stays as-is

### Clarification Loop

When `state.low_confidence_fields` is non-empty, ConversationAgent reads it on the
NEXT turn and weaves ONE natural clarification question into its response.

Not a form. Not a list. One question, phrased conversationally, only if it fits the
current topic naturally.

Example:
- Compressor this turn: `diet_type` confidence=0.60, uncertainty="not confirmed as primary diet"
- ConversationAgent next turn: "By the way, is kibble Luna's main diet, or does she eat
  other things too? Knowing helps me give more accurate advice."

**ConversationAgent constraint:** Ask at most one clarification question per turn.
Priority order: weight_kg > age_years > current_conditions > breed > everything else.
This is a soft instruction in Agent 1's system prompt — not hard logic in code.

---

## 8. The Compressor Prompt — Structure

```
SYSTEM:
You are a structured fact extractor for a pet health app. Your only job is to extract
factual claims about the pet from the user message. Return ONLY valid JSON. No explanation.

EXTRACTION RULES:
1. Extract ONLY facts explicitly stated. Never infer or guess.
2. time_scope: "current" if present tense, "past" if past tense, "unknown" if unclear.
3. source_rank: "vet_record" if user mentions vet/doctor/test result. Else "explicit_owner".
4. Confidence scoring:
   - 0.95 — hard specific fact ("vet confirmed exactly 4.2kg")
   - 0.85 — stated confidently ("Luna weighs 4kg")
   - 0.75 — mild hedging ("about 4kg", "roughly", "around")
   - 0.60 — clear uncertainty ("I think", "maybe", "probably")
   - 0.50 — speculative or second-hand
5. uncertainty: write a plain-text explanation of why confidence is not 1.0. Empty string if confident.
6. Negative facts are valid: "no allergies" → key="allergies", value="none confirmed".
7. Normalize values: "4 kilos" → value="4", key="weight_kg". Always use standard units.
8. timestamp: ISO string only when user explicitly states a time. Otherwise null.
9. Extract ALL facts in one call. Multiple facts = multiple entries in the array.
10. If nothing extractable: return {"is_entity": false, "facts": []}.

PREFERRED KEY NAMES (use these when applicable):
breed, age_years, weight_kg, sex, neutered, diet_type, food_brand, allergies,
current_conditions, past_conditions, current_medications, past_medications,
vaccination_status, vet_name, vet_clinic, energy_level, temperament,
behavioral_traits, appetite, activity_level
For anything else: use descriptive snake_case.

OUTPUT FORMAT (strict):
{
  "is_entity": bool,
  "facts": [
    {
      "key": str,
      "value": str,
      "confidence": float,
      "source_rank": str,
      "time_scope": "current"|"past"|"unknown",
      "uncertainty": str,
      "source_quote": str,
      "timestamp": str|null
    }
  ]
}

USER:
Pet: {pet_name} | Species: {pet_species} | Age: {pet_age} | Sex: {pet_sex} | Weight: {pet_weight}

Recent conversation context (for pronoun resolution only — do not extract facts from this):
{last_3_turns}

Message to extract from:
"{user_message}"
```

**LLM settings:**
- `temperature = 0.0` — deterministic extraction
- `max_tokens = 400` — enough for ~5 facts in JSON
- Same Azure OpenAI deployment as Agent 1

---

## 9. Agent 1 Dependency — Structured Output

**Option C requires Agent 1 to return structured JSON**, not plain text. This is a
change to Agent 1's current output format.

Agent 1 currently returns a plain string (`reply: str`).
After this change it must return:

```json
{
  "reply": "That sounds like it could be an ear infection...",
  "is_entity": true
}
```

Agent 1 is already well-positioned to make this judgment — it has the full pet context
and has just processed the message. Adding `is_entity` adds trivial overhead.

This change must happen BEFORE the Compressor is wired into the pipeline.
Track as a dependency: **Agent 1 structured output** (part of Phase 1B setup).

---

## 10. Open Questions — Resolved

| Question | Decision |
|---|---|
| How much recent history? | 3 turns. Enough for pronoun resolution. |
| Confidence threshold for writing to DB? | > 0.70 |
| Should Compressor normalize values? | Yes. "4 kilos" → weight_kg: 4. |
| Same fact across turns with different confidence? | Aggregator's problem. Compressor always extracts. |
| Batch-run on historical messages? | Not needed. Development phase uses hardcoded Luna data. Full pipeline in place before real users. |
| Multi-entity messages? | Yes — all facts extracted in one call, multiple entries in facts array. |
| `low_confidence_fields` lifecycle? | Last turn only. Replaced each turn. No accumulation, no expiry logic. |

---

## 11. What We Are NOT Doing (And Why)

| Thing | Why Not |
|---|---|
| Gap list in Compressor prompt | Not its job. Compressor extracts everything. Aggregator decides what to keep. Same field (energy level) can appear many times legitimately — always extract it. |
| Full conversation history | Too many tokens. 3 turns covers all realistic pronoun chains. |
| Regex as a gate | Violates the core principle. May use as soft hints in the future, never as a skip decision. |
| Compressor on Agent 1 replies | Agent 1's text is generated from context we already have. No new facts. Only user messages contain new facts. |
| Strict key enum | Kills flexibility. Pet chats surface unexpected facts. Preferred taxonomy + freeform is the right balance. |
| Batch retroactive extraction | Not needed in development phase. Full pipeline will exist before real users arrive. |

---

## 12. Phase 1B Build Order

1. **Modify Agent 1** → return structured JSON with `is_entity` field (dependency for Option C)
2. **`app/storage/file_store.py`** → JSON read/write helpers. Build and test independently.
3. **`app/agents/compressor.py`** → LLM extraction with structured output. Test with hardcoded messages.
4. **`app/agents/aggregator.py`** → pure deterministic merge logic. Test with hardcoded facts.
5. **Introduce `AgentState` dataclass** → wire through the pipeline replacing loose function args.
6. **Wire fire-and-forget in `app/main.py`** → `asyncio.create_task()` when `is_entity=true`
7. **Manual end-to-end test** → say "Luna weighs 4kg", check `data/fact_log.json` and `data/active_profile.json`

---

## 13. Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-03-07 | Gate: Option C — ConversationAgent outputs `is_entity` | Most accurate. Agent 1 has full context. Satisfies the "never skip a fact" principle. |
| 2026-03-07 | Regex: deferred entirely | May add later as soft hints only, never as a gate. |
| 2026-03-07 | Add `time_scope` field (was `temporal`) | Critical for Aggregator — past vs current conditions are fundamentally different. |
| 2026-03-07 | Add `uncertainty` field | Turns a number (0.60) into a readable explanation. Better audit trail. Helps Agent 1 phrase clarification questions. |
| 2026-03-07 | Add `timestamp` field | Null unless user explicitly states a time. Preserves temporal precision when available. |
| 2026-03-07 | Rename `field` → `key` | Cleaner vocabulary. `field` is overloaded in Python. |
| 2026-03-07 | Confidence threshold: > 0.70 | Tunable. Start here, adjust after seeing real data. |
| 2026-03-07 | `low_confidence_fields`: last turn only | No accumulation, no expiry logic, no cleanup. Simple and predictable. |
| 2026-03-07 | Include 3 turns of recent history | Enough for pronoun resolution. Full history is too expensive. |
| 2026-03-07 | Batch retroactive extraction: not needed | Development uses hardcoded Luna data. Full pipeline in place before real users. |
| 2026-03-07 | Key names: preferred taxonomy + freeform | Captures unexpected facts. Aggregator normalizes collisions. |
