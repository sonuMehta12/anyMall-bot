// Development: VITE_API_URL=http://127.0.0.1:8000 (set in .env.development or via CLI)
// Production:  empty string — requests go to the same server serving the frontend
const BASE = import.meta.env.VITE_API_URL ?? ''

// POST /chat
export async function sendMessage({ sessionId, message }) {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      message,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} — failed to send message`)
  const data = await res.json()

  // ── Agent 1 — logged immediately (synchronous, in the /chat response) ───────
  console.group(
    `%c[Agent 1]  intent=${data.intent_type?.toUpperCase()}  urgency=${data.urgency}  is_entity=${data.is_entity}`,
    'color:#7c3aed; font-weight:bold'
  )
  console.log('reply       →', data.message)
  console.log('is_entity   →', data.is_entity, '  (true = message had extractable pet facts)')
  console.log('intent_type →', data.intent_type)
  console.log('urgency     →', data.urgency)
  console.log('guardrailed →', data.was_guardrailed)
  if (data.redirect) {
    console.log('redirect    →', data.redirect)
  }
  console.groupEnd()

  // ── Agent 2 (Compressor) — logged after 8s (runs fire-and-forget in background) ──
  // The Compressor starts AFTER the reply is sent, so we poll /debug/facts
  // 8 seconds later to give it time to finish its LLM call and write to fact_log.json.
  setTimeout(async () => {
    try {
      const factsRes = await fetch(`${BASE}/debug/facts?session_id=${data.session_id}&limit=10`)
      const factsData = await factsRes.json()

      if (factsData.facts?.length > 0) {
        console.group(
          `%c[Agent 2]  Compressor extracted ${factsData.count} fact(s)`,
          'color:#059669; font-weight:bold'
        )
        factsData.facts.forEach(f => {
          const flag = f.needs_clarification ? '⚠ needs clarification' : '✓ high confidence'
          console.log(
            `  ${f.key.padEnd(24)} = "${f.value}"`,
            `| conf=${f.confidence}`,
            `| scope=${f.time_scope}`,
            `| ${flag}`
          )
        })
        console.groupEnd()
      } else {
        console.log(
          '%c[Agent 2]  Compressor — no facts extracted  (is_entity=false or nothing extractable)',
          'color:#6b7280'
        )
      }
    } catch (e) {
      console.warn('[Agent 2] Could not fetch /debug/facts:', e)
    }

    // ── Agent 3 (Aggregator) — logged right after Agent 2 (same poll window) ──
    // The Aggregator runs immediately after the Compressor in _run_background(),
    // so the profile should be updated by the time we check.
    try {
      const profileRes = await fetch(`${BASE}/debug/profile`)
      const profileData = await profileRes.json()

      if (profileData.status === 'ok' && profileData.field_count > 0) {
        console.group(
          `%c[Agent 3]  Aggregator — active profile (${profileData.field_count} field(s))`,
          'color:#d97706; font-weight:bold'
        )
        Object.entries(profileData.profile).forEach(([key, entry]) => {
          if (key.startsWith('_')) return // skip metadata like _pet_history
          const status = entry.status ? `status=${entry.status}` : ''
          const change = entry.change_detected ? `  change="${entry.change_detected}"` : ''
          console.log(
            `  ${key.padEnd(24)} = "${entry.value}"`,
            `| conf=${entry.confidence}`,
            `| ${status}${change}`
          )
        })
        console.groupEnd()
      } else {
        console.log(
          '%c[Agent 3]  Aggregator — no active profile yet',
          'color:#6b7280'
        )
      }
    } catch (e) {
      console.warn('[Agent 3] Could not fetch /debug/profile:', e)
    }
  }, 8000)

  return data
}
