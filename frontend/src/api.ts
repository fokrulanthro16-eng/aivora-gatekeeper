import type {
  AggregatorStatusResponse,
  CheckUsageResponse,
} from './types'

// VITE_API_BASE_URL is the canonical var. VITE_API_URL is a legacy fallback.
const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  (import.meta.env.VITE_API_URL as string | undefined) ??
  'http://localhost:8000'

const TIMEOUT_MS = 5_000

export { API_BASE }

function withTimeout(ms: number): AbortSignal {
  return AbortSignal.timeout(ms)
}

// ── Aggregator status (primary polling endpoint) ──────────────────────────────

export async function fetchAggregatorStatus(): Promise<AggregatorStatusResponse> {
  const res = await fetch(`${API_BASE}/v1/aggregator/status`, {
    signal: withTimeout(TIMEOUT_MS),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<AggregatorStatusResponse>
}

// ── Usage check (activate-billing-shield button) ──────────────────────────────

export async function checkUsage(
  userUuid: string,
  provider = 'openai',
  model = 'gpt-4o-mini',
): Promise<CheckUsageResponse> {
  const res = await fetch(`${API_BASE}/v1/aggregator/check-usage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_uuid:         userUuid,
      provider,
      model,
      estimated_tokens:  50,
      estimated_cost:    0.0001,
    }),
    signal: withTimeout(TIMEOUT_MS),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<CheckUsageResponse>
}
