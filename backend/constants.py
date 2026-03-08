# backend/constants.py
#
# Business-logic constants for AnyMall-chan.
# No secrets live here — only facts about how the domain works.
# Safe to commit. Never import config.py or .env values here.
#
# Sections:
#   1. Fact-confidence priority ranks
#   2. Recency decay
#   3. Confidence thresholds
#   4. Entity extraction patterns (regex)
#   5. Medical / nutritional keyword lists
#   6. Guardrail phrase lists
#   7. Conversation limits
#   8. Field display labels
#   9. Gap-question hints

import re


# Sections (continued):
#  10. Intent type constants
#  11. Urgency level constants
#  12. Intent classification note (LLM-based, no keyword lists)


# ── 1. Fact-Confidence Priority Ranks ─────────────────────────────────────────
#
# Every fact about a pet has one of these ranks.  Higher rank = more trustworthy
# source.  When two facts for the same field conflict, the higher rank wins.
# When ranks tie, recency breaks the tie (see Section 2).
#
# Rank values are integers so you can compare them directly:
#   PRIORITY_RANKS["explicit_owner"] > PRIORITY_RANKS["inferred"]  → True

PRIORITY_RANKS: dict[str, int] = {
    "vet_record":       5,   # User uploaded a vet document — gold standard
    "explicit_owner":   4,   # User stated the fact directly ("Luna eats raw food")
    "confirmed":        3,   # User confirmed something Agent 1 asked ("yes, that's right")
    "inferred":         2,   # Agent 1 deduced it from context, not a direct statement
    "default":          1,   # System fallback — used when nothing else is known
}

# Ordered from highest to lowest — useful when you need to iterate in rank order.
RANK_ORDER: list[str] = [
    "vet_record",
    "explicit_owner",
    "confirmed",
    "inferred",
    "default",
]

# All priority keys as a set — lets you validate a rank string quickly.
ALL_PRIORITY_KEYS_ORDERED: list[str] = RANK_ORDER  # alias for readability

# Reverse map: rank name → numeric value (same as PRIORITY_RANKS, kept for
# clarity when the caller has a name and needs the number).
KEY_TO_RANK: dict[str, int] = PRIORITY_RANKS


# ── 2. Recency Decay ───────────────────────────────────────────────────────────
#
# A fact's effective confidence decays the older it is.  We multiply the raw
# confidence score by a decay multiplier based on how many days old the fact is.
#
# STALE_THRESHOLD_DAYS: after this many days, a fact is considered "stale" and
# Agent 1 may ask the user to re-confirm it.
#
# RECENCY_DECAY_TABLE: list of (max_age_days, multiplier) pairs, checked in
# order.  First matching bucket wins.  Last entry covers everything older.
#
# Example: a fact that is 20 days old gets multiplier 0.90 (10 % decay).

STALE_THRESHOLD_DAYS: int = 90  # 3 months

RECENCY_DECAY_TABLE: list[tuple[int, float]] = [
    (7,   1.00),   # 0–7 days old   → no decay
    (30,  0.95),   # 8–30 days old  → 5 % decay
    (60,  0.90),   # 31–60 days old → 10 % decay
    (90,  0.80),   # 61–90 days old → 20 % decay
    (9999, 0.60),  # 91+ days old   → 40 % decay (stale)
]

# Multipliers only, indexed the same way — convenient when you just need the
# list of possible multiplier values.
DECAY_MULTIPLIERS: list[float] = [row[1] for row in RECENCY_DECAY_TABLE]


# ── 3. Confidence Thresholds ───────────────────────────────────────────────────
#
# Agent 1 computes a 0–100 confidence score for each profile field.
# The score drives the Confidence Bar colour in the Flutter UI:
#   Green  (≥ GREEN_THRESHOLD)  → the app trusts this field
#   Yellow (≥ YELLOW_THRESHOLD) → partially known, may need confirmation
#   Red    (< YELLOW_THRESHOLD) → unknown or very uncertain
#
# Agent 1 formula (used in Phase 0 conceptually, fully wired in Phase 1):
#   score = rank_weight * decay_multiplier * 100
#
# Rank weights map rank name → base weight (0–1).

CONFIDENCE_GREEN_THRESHOLD: int = 75
CONFIDENCE_YELLOW_THRESHOLD: int = 40

RANK_WEIGHTS: dict[str, float] = {
    "vet_record":       1.00,
    "explicit_owner":   0.85,
    "confirmed":        0.70,
    "inferred":         0.50,
    "default":          0.20,
}

# Maximum confidence a single field can reach (cap, not a threshold).
CONFIDENCE_MAX: int = 100
CONFIDENCE_MIN: int = 0


# ── 4. Entity Extraction Patterns (regex) ─────────────────────────────────────
#
# Used in guardrails.py → classify_intent() to detect entities in user messages
# without making an LLM call.  All patterns are pre-compiled for performance.
#
# Each key is an entity type.  Each value is a compiled regex.
# Patterns are intentionally broad — false positives are acceptable here because
# the intent classifier just flags the message; it does not act on it.

ENTITY_PATTERNS: dict[str, re.Pattern] = {
    # Age mentions: "2 years old", "8 months", "3-year-old"
    "age": re.compile(
        r"\b(\d+)\s*[-]?\s*(year|month|week|day)s?\s*(old)?\b",
        re.IGNORECASE,
    ),

    # Weight mentions: "4 kg", "12 lbs", "4.5 kilograms"
    "weight": re.compile(
        r"\b(\d+(\.\d+)?)\s*(kg|kgs|kilogram|kilograms|lb|lbs|pound|pounds)\b",
        re.IGNORECASE,
    ),

    # Diet / food mentions: "raw food", "kibble", "wet food", "grain-free"
    "diet": re.compile(
        r"\b(raw\s*food|raw\s*diet|kibble|dry\s*food|wet\s*food|canned\s*food"
        r"|grain[- ]free|home[- ]cooked|vegan|vegetarian|barf\s*diet)\b",
        re.IGNORECASE,
    ),

    # Breed mentions — common breeds; extend as needed
    "breed": re.compile(
        r"\b(labrador|retriever|golden|poodle|beagle|bulldog|husky|shiba\s*inu"
        r"|german\s*shepherd|rottweiler|dachshund|chihuahua|boxer|pug|maltese"
        r"|persian|siamese|bengal|maine\s*coon|ragdoll|sphynx|british\s*shorthair)\b",
        re.IGNORECASE,
    ),

    # Medication / supplement mentions
    "medication": re.compile(
        r"\b(antibiotic|antibiotics|steroid|steroids|insulin|flea\s*treatment"
        r"|tick\s*treatment|wormer|dewormer|supplement|vitamins?|probiotic"
        r"|apoquel|benadryl|rimadyl|meloxicam)\b",
        re.IGNORECASE,
    ),

    # Symptom mentions — triggers medical-concern flag
    "symptom": re.compile(
        r"\b(vomit|vomiting|diarrhea|diarrhoea|lethargy|lethargic|limping"
        r"|scratching|itching|swelling|bleeding|seizure|seizures|coughing"
        r"|sneezing|not\s*eating|loss\s*of\s*appetite|weight\s*loss|pale\s*gums)\b",
        re.IGNORECASE,
    ),
}


# ── 5. Medical & Nutritional Keyword Lists ────────────────────────────────────
#
# MEDICAL_KEYWORDS: if any of these appear in a user message, Agent 1 must add
# a soft disclaimer ("I'm not a vet — please consult your veterinarian").
# This is a guardrail, not a block.
#
# NUTRITIONAL_KEYWORDS: used to detect diet-related questions so Agent 1 can
# respond with appropriate context instead of generic chat.

MEDICAL_KEYWORDS: list[str] = [
    "diagnose", "diagnosis", "treat", "treatment", "cure", "surgery",
    "medication", "medicine", "drug", "dose", "dosage", "prescription",
    "vet", "veterinarian", "clinic", "hospital", "emergency",
    "infection", "virus", "bacteria", "parasite", "cancer", "tumor",
    "fracture", "broken bone", "abscess", "wound", "blood",
]

NUTRITIONAL_KEYWORDS: list[str] = [
    "food", "diet", "feed", "feeding", "meal", "nutrition", "nutrient",
    "protein", "fat", "carbohydrate", "calorie", "portion", "serving",
    "raw", "kibble", "wet food", "dry food", "grain", "allergy",
    "intolerance", "supplement", "probiotic", "omega",
]


# ── 6. Guardrail Phrase Lists ──────────────────────────────────────────────────
#
# BLOCKED_MEDICAL_JARGON: phrases that sound like a vet diagnosis.
# If Agent 1's response contains any of these, guardrails.py rewrites the
# sentence to a softer, non-diagnostic version.
# These are patterns that a non-vet AI should never output as fact.

BLOCKED_MEDICAL_JARGON: list[str] = [
    "you should give",
    "the dog has",
    "the cat has",
    "this is definitely",
    "this is clearly",
    "i diagnose",
    "diagnosed with",
    "you must administer",
    "administer",
    "prescribe",
    "this confirms",
]

# PREACHY_PHRASES: Agent 1 should never lecture the user.
# If the response contains any of these, guardrails strips or softens them.
# We want warm and helpful, not moralising.

PREACHY_PHRASES: list[str] = [
    "you should always",
    "you must always",
    "it is very important that you",
    "i strongly urge",
    "i strongly advise",
    "never feed your pet",
    "you really need to",
    "as a responsible pet owner",
    "responsible ownership",
]


# ── 7. Conversation Limits ────────────────────────────────────────────────────
#
# Agent 1 is allowed to ask gap-filling questions, but not too many at once.
# If it asks too many questions the user feels interrogated, not helped.
#
# MAX_QUESTIONS_PER_SESSION: hard ceiling on how many profile-gap questions
# Agent 1 can ask in a single session (not per message — per full session).
#
# MAX_QUESTIONS_PER_MESSAGE: Agent 1 should only ask one question per reply.
# We track this to let guardrails catch a response that asks two questions
# before sending it to the user.

MAX_QUESTIONS_PER_SESSION: int = 5
MAX_QUESTIONS_PER_MESSAGE: int = 1


# ── 8. Field Display Labels ───────────────────────────────────────────────────
#
# Maps internal field keys (as stored in the DB / dummy_context) to the
# human-readable label shown in the Flutter UI.
# Agent 1 also uses these when constructing gap-question hints (Section 9).

FIELD_LABELS: dict[str, str] = {
    "name":             "Name",
    "species":          "Species",
    "breed":            "Breed",
    "age":              "Age",
    "sex":              "Sex",
    "weight":           "Weight",
    "diet_type":        "Diet",
    "food_brand":       "Food Brand",
    "medications":      "Medications",
    "past_medications": "Past Medications",
    "energy_level":     "Energy Level",
    "neutered_spayed":  "Neutered / Spayed",
    "chronic_illness":  "Chronic Illness",
    "past_conditions":  "Past Conditions",
    "allergies":        "Known Allergies",
    "vaccinations":     "Vaccination Status",
    "vet_name":         "Vet / Clinic",
    "last_vet_visit":   "Last Vet Visit",
    "appetite":         "Appetite",
    "activity_level":   "Activity Level",
    "temperament":      "Temperament",
    "behavioral_traits": "Behavioral Traits",
    "microchipped":     "Microchipped",
    "insurance":        "Pet Insurance",
}


# ── 8b. Full Field List ─────────────────────────────────────────────────────
#
# Canonical list of ALL known field keys, derived from FIELD_LABELS.
# Used by context_builder.py to compute gap_list (which fields are missing).
# The order matches FIELD_LABELS insertion order.

FULL_FIELD_LIST: list[str] = list(FIELD_LABELS.keys())

# Fields that are considered HIGH-PRIORITY gaps.  Agent 1 should try to fill
# these before lower-priority ones.  Ordered by importance.
HIGH_PRIORITY_FIELDS: list[str] = [
    "species",
    "breed",
    "age",
    "weight",
    "diet_type",
    "medications",
    "chronic_illness",
    "allergies",
]


# ── 9. Gap-Question Hints ─────────────────────────────────────────────────────
#
# When Agent 1 detects a gap (a field with low confidence), it uses these hints
# to construct a natural question.  The hint is a short phrase — Agent 1 wraps
# it in a conversational sentence.
#
# Keys match FIELD_LABELS keys.  Not every field needs a hint (Agent 1 can
# improvise for less common fields), but having hints makes questions consistent.

GAP_QUESTION_HINTS: dict[str, str] = {
    "age":            "how old {name} is",
    "weight":         "how much {name} weighs",
    "breed":          "what breed {name} is",
    "diet_type":      "what {name} usually eats",
    "medications":    "whether {name} is on any medications or supplements",
    "energy_level":   "how active {name} generally is",
    "neutered_spayed": "whether {name} has been neutered or spayed",
    "chronic_illness": "whether {name} has any ongoing health conditions",
    "allergies":      "whether {name} has any known allergies",
    "vaccinations":   "whether {name}'s vaccinations are up to date",
    "last_vet_visit": "when {name} last saw a vet",
    "vet_name":       "which vet or clinic {name} goes to",
    "appetite":       "how {name}'s appetite has been lately",
    "activity_level": "how active {name} generally is",
}
# Usage example (in conversation.py):
#   hint = GAP_QUESTION_HINTS["diet_type"].format(name="Luna")
#   → "what Luna usually eats"
#   Agent 1 then wraps it: "By the way, could you tell me what Luna usually eats?"


# ── 10. Intent Type Constants ─────────────────────────────────────────────────
#
# These strings identify what kind of message the user sent.
# Used by classify_intent() and build_deeplink() to decide the routing.
#
# Always use these constants — never type "health" or "food" as a raw string.
# If we rename intents later, one change here fixes everything.

INTENT_GENERAL: str = "general"   # normal conversation — no redirect
INTENT_HEALTH: str  = "health"    # medical concern → redirect to Health module
INTENT_FOOD: str    = "food"      # diet / nutrition question → redirect to Food module


# ── 11. Urgency Level Constants ───────────────────────────────────────────────
#
# Used inside the deeplink payload to tell the mobile app how urgently to
# present the redirect button.
#   high   → red button, vibration alert (vomiting, seizure, etc.)
#   medium → standard orange button (limping, lethargy, etc.)
#   low    → soft suggestion (general diet question, routine check-in)

URGENCY_HIGH: str   = "high"
URGENCY_MEDIUM: str = "medium"
URGENCY_LOW: str    = "low"


# ── 12. Intent classification ─────────────────────────────────────────────────
#
# Intent classification (health/food routing and urgency levels) is handled by
# IntentClassifier (app/agents/intent_classifier.py) via LLM — no keyword lists
# needed here.
