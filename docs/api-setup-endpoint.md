# `GET /api/v1/setup` — API Reference

Returns the confidence score for a pet profile and suggested home-screen questions. Call this on app mount and whenever you need to refresh the confidence bar.

---

## Request

```
GET /api/v1/setup?pet_id={id}
```

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `X-User-Code` | Yes | Authenticated user identifier |

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pet_id` | integer | Yes | Pet ID to score. Repeat for a second pet. |
| `language` | string | No | `"EN"`, `"JA"`, or `"auto"` (default: `"auto"`) |

**Single pet**
```
GET /api/v1/setup?pet_id=101
```

**Two pets (score is averaged)**
```
GET /api/v1/setup?pet_id=101&pet_id=102
```

**With explicit language**
```
GET /api/v1/setup?pet_id=101&language=JA
```

---

## Response

**`200 OK`**

```json
{
  "status": "ok",
  "confidence_score": 72,
  "confidence_color": "yellow",
  "suggested_questions": [
    "What should I feed my dog?",
    "How much exercise does my dog need?",
    "Is my dog's weight healthy?",
    "What vaccinations does my dog need?"
  ],
  "questions_cached": true,
  "questions_generated_at": "2026-03-25T10:00:00+00:00"
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` on success |
| `confidence_score` | integer | `0–100`. How well the system knows this pet. |
| `confidence_color` | string | `"green"` (80–100) · `"yellow"` (50–79) · `"red"` (0–49) |
| `suggested_questions` | string[] | 4 question strings for home-screen chips |
| `questions_cached` | boolean | `true` = LLM-generated, `false` = evergreen fallback |
| `questions_generated_at` | string | ISO 8601 timestamp, or `""` if using fallback questions |

> **Note:** `suggested_questions` always returns exactly 4 items. If no personalised questions have been generated yet, evergreen fallback questions are returned — safe to render immediately, no loading state needed.

---

## Error Responses

| Status | Reason |
|--------|--------|
| `400 Bad Request` | `pet_id` query parameter missing |
| `401 Unauthorized` | `X-User-Code` header missing |
| `502 Bad Gateway` | Could not fetch pet data from AALDA |
| `503 Service Unavailable` | Database unavailable — retry the request |

---

## Language Resolution

The `language` parameter controls which language the suggested questions are returned in. Resolution order:

1. Explicit `language` param (`"EN"` or `"JA"`) → used as-is
2. `"auto"` + user has a stored language preference → use stored preference
3. `"auto"` + no stored preference → defaults to `"JA"`

---

## JavaScript Example

```js
async function loadSetup(petIds, userCode, language = 'auto') {
  const ids = Array.isArray(petIds) ? petIds : [petIds]
  const query = ids.map(id => `pet_id=${id}`).join('&') + `&language=${language}`

  const res = await fetch(`/api/v1/setup?${query}`, {
    headers: { 'X-User-Code': userCode },
  })

  if (!res.ok) throw new Error(`${res.status} — failed to fetch setup`)
  return res.json()
}

// Usage
const data = await loadSetup([101], 'USER-CODE-HERE')

console.log(data.confidence_score)      // 72
console.log(data.confidence_color)      // "yellow"
console.log(data.suggested_questions)   // ["...", "...", "...", "..."]
```

---

## Notes

- `/api/v1/confidence` is a backward-compatible alias that returns only `confidence_score` and `confidence_color` — **use `/setup` for all new integrations**.
- When passing two pet IDs, the confidence score is the arithmetic average of both pets' scores.
- The `X-User-Code` header must be present on every request or the server returns `401`.
