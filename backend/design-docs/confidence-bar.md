# Confidence Bar — Design Document

## Purpose

A 0-100 score showing how well AnyMall-chan knows the pet.
User-facing engagement mechanic — NOT a data-completion percentage.

## Formula

```
score = sum(field_confidence × decay × importance_weight) / 46 × 100
```

Three signals per field, all from existing data (no LLM calls):

| Signal | Source | Range |
|--------|--------|-------|
| field_confidence | Compressor/Aggregator `confidence` | 0.0–1.0 |
| decay | Exponential from `updated_at` age | 0.3–1.0 |
| importance_weight | Static tier lookup | 1, 2, or 3 |

## Why No "Depth" Component

The PRD proposed a depth score based on answer word count. We dropped it because:
- The Compressor's per-field confidence already encodes information quality.
- "yes" for neutered_spayed is a perfect answer — penalizing short answers is wrong.
- Measuring depth requires either word-count heuristics (unreliable) or an LLM call (wasteful).

## Field Tiers (22 scored fields)

`name` excluded — always known from onboarding.

| Tier | Weight | Count | Fields |
|------|--------|-------|--------|
| A | 3 | 8 | species, breed, age, weight, diet_type, medications, chronic_illness, allergies |
| B | 2 | 7 | sex, neutered_spayed, energy_level, appetite, vaccinations, past_conditions, food_brand |
| C | 1 | 8 | temperament, behavioral_traits, activity_level, vet_name, last_vet_visit, microchipped, insurance, past_medications |

Total weight = (8×3) + (7×2) + (8×1) = **46**

Filling all Tier A + B at full confidence = 38/46 = 82.6% → green.

## Decay Categories

| Category | Half-life | Fields |
|----------|-----------|--------|
| Static | ∞ | species, breed, sex, neutered_spayed, microchipped |
| Slow | 180 days | allergies, chronic_illness, temperament, behavioral_traits, insurance |
| Medium | 90 days | diet_type, food_brand, medications, vaccinations, vet_name, last_vet_visit |
| Fast | 45 days | weight, age, energy_level, appetite, activity_level, past_conditions, past_medications |

### Life Stage Multiplier (divides half-life)

| Life Stage | Fast | Medium | Slow |
|------------|------|--------|------|
| puppy/kitten | 2.0 | 1.5 | 1.25 |
| junior | 1.5 | 1.25 | 1.0 |
| adult | 1.0 | 1.0 | 1.0 |
| senior | 1.5 | 1.25 | 1.0 |

### Decay Function

```python
effective_half_life = base_half_life / life_stage_multiplier
decay = max(0.3, 0.5 ** (age_days / effective_half_life))
```

Floor at 0.3 — old data is still better than nothing.

## Color Thresholds

| Color | Range | Meaning |
|-------|-------|---------|
| Green | 80–100 | AnyMall-chan knows {name} well |
| Yellow | 50–79 | Some gaps or outdated info |
| Red | 0–49 | Significant gaps |

## Computation Strategy

**On-the-fly, not persisted.** The calculation is pure arithmetic over 22 fields (sub-ms).
Persisting would create immediate staleness because decay depends on `datetime.now()`.
Computed fresh each `/chat` request from the current `active_profile.json`.

## Edge Cases

- Seed data without `updated_at` → no decay (treated as fresh)
- Confidence as integer (80) → normalized to 0.8
- Extra keys in active_profile (vomiting, limping) → ignored
- `_pet_history` metadata key → skipped
- Unknown life_stage → defaults to "adult"

## Files

- `app/services/confidence_calculator.py` — pure functions, all config + calculation
- `app/routes/chat.py` — `ChatResponse` includes `confidence_score` (int) and `confidence_color` (str)

## Decision Log

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Drop depth component | Per-field confidence already captures quality |
| 2 | Exponential decay, not step-based | Smoother, more honest representation |
| 3 | 0.3 floor on decay | Old info > no info |
| 4 | Compute on-the-fly | Decay is time-dependent, persisting = immediate staleness |
| 5 | All config in confidence_calculator.py | Only one consumer, avoids bloating constants.py |
