// ── Shared subsystem types ────────────────────────────────────────────────────

export interface CircuitBreakerSnapshot {
  name: string
  state: string
  failure_count: number
  half_open_successes: number
  opened_at: number | null
  last_failure_at: number | null
  total_calls: number
  total_successes: number
  total_failures: number
  total_fallbacks: number
}

export interface CacheStats {
  total_entries: number
  max_entries: number
  hits: number
  misses: number
  evictions: number
}

// ── Aggregator status ─────────────────────────────────────────────────────────

export type AggregatorSystemStatus =
  | 'protected'
  | 'supabase_not_connected'
  | 'firewall_off'

export interface AggregatorStats {
  total_calls_blocked: number
  total_cost_saved_usd: number
  active_sessions: number
  active_tiers: number
}

export interface AggregatorStatusResponse {
  app_name: string
  version: string
  env: string
  status: AggregatorSystemStatus
  supabase_available: boolean
  openrouter_configured: boolean
  polar_configured: boolean
  demo_mode: boolean
  stats: AggregatorStats
  circuit_breaker_state: string
  cache_entries: number
}

// ── Usage check ───────────────────────────────────────────────────────────────

export interface CheckUsageResponse {
  allowed: boolean
  reason: string
  remaining_messages: number
  remaining_budget_usd: number
  estimated_cost: number
  provider: string
  model: string
  user_uuid: string
}

// ── Gatekeeper (legacy — kept for /gatekeeper/status compatibility) ───────────

export interface GatekeeperStatusResponse {
  app_name: string
  version: string
  env: string
  supabase_available: boolean
  demo_mode: boolean
  circuit_breaker: CircuitBreakerSnapshot
  cache: CacheStats
}

export interface QuotaDecision {
  allowed: boolean
  remaining_tokens: number
  reason: string
  estimated_cost: number
  degraded_mode: boolean
}

export interface ProtectResponse {
  decision: QuotaDecision
  user_uuid: string
  cache_hit: boolean
}

// ── Dashboard display ─────────────────────────────────────────────────────────

export type ShieldStatus = 'loading' | 'protected' | 'supabase_not_connected' | 'firewall_off'

export interface DashboardMetrics {
  openrouterCallsBlocked: number
  estimatedMoneySaved: number
  /** Remaining USD budget for the current billing month. -1 = not available. */
  monthlyBudgetRemaining: number
  /** Active subscription tier name, e.g. "Pro". "--" when unknown. */
  currentTier: string
}
