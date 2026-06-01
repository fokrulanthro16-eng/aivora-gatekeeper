import { useState, useEffect, useCallback } from 'react'
import type { ShieldStatus, DashboardMetrics, AggregatorStatusResponse, CheckUsageResponse } from './types'
import { fetchAggregatorStatus, checkUsage, API_BASE } from './api'

// ── Runtime mode ──────────────────────────────────────────────────────────────
const IS_DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

const DEMO_USER_UUID = '00000000-0000-0000-0000-000000000001'
const STATUS_POLL_MS = 30_000
const DEMO_DRIFT_MS  = 4_000
const SIM_STEP_MS    = 700   // delay between simulation steps

const ZERO_METRICS: DashboardMetrics = {
  openrouterCallsBlocked: 0,
  estimatedMoneySaved:    0,
  monthlyBudgetRemaining: -1,
  currentTier:            '--',
}

const DEMO_METRICS: DashboardMetrics = {
  openrouterCallsBlocked: 892,
  estimatedMoneySaved:    247,
  monthlyBudgetRemaining: 17.32,
  currentTier:            'Pro',
}

// ── Simulation step definition ────────────────────────────────────────────────

interface SimStep {
  icon: string
  label: string
  detail: string
  variant: 'neutral' | 'success' | 'error' | 'info'
}

const SIM_BASE: readonly SimStep[] = [
  { icon: '👤', label: 'User Request',   detail: 'POST /v1/aggregator/proxy-openrouter',           variant: 'neutral' },
  { icon: '💲', label: 'Cost Estimated', detail: '$0.004 · openai/gpt-4o-mini · 220 tokens',       variant: 'info'    },
  { icon: '🔍', label: 'Quota Check',    detail: 'check_and_consume_ai_usage() — FOR UPDATE lock', variant: 'info'    },
]

const SIM_ALLOWED: SimStep = {
  icon: '✅', label: 'Allowed',
  detail: 'Remaining: 847 messages · $18.43 budget · 9 780 tokens',
  variant: 'success',
}

const SIM_BLOCKED: SimStep = {
  icon: '❌', label: 'Blocked',
  detail: 'monthly_budget_exceeded → HTTP 429 returned to client',
  variant: 'error',
}

const SIM_OPENROUTER: SimStep = {
  icon: '🤖', label: 'OpenRouter',
  detail: 'Request forwarded → gpt-4o-mini → response returned ✓',
  variant: 'success',
}

// Steps for each outcome
function getSimSteps(blocked: boolean): readonly SimStep[] {
  return blocked
    ? [...SIM_BASE, SIM_BLOCKED]
    : [...SIM_BASE, SIM_ALLOWED, SIM_OPENROUTER]
}

// ── Helper functions ──────────────────────────────────────────────────────────

function metricsFromStatus(
  data: AggregatorStatusResponse,
  usageCheck: CheckUsageResponse | null,
): DashboardMetrics {
  return {
    openrouterCallsBlocked: data.stats.total_calls_blocked,
    estimatedMoneySaved:    Math.round(data.stats.total_cost_saved_usd),
    monthlyBudgetRemaining: usageCheck?.remaining_budget_usd ?? -1,
    currentTier:            usageCheck ? 'Pro' : '--',
  }
}

function systemStatusToShield(
  backendOnline: boolean,
  data: AggregatorStatusResponse | null,
  userActivated: boolean,
): ShieldStatus {
  if (!backendOnline) return 'firewall_off'
  if (!data) return 'loading'
  if (data.status === 'supabase_not_connected') return 'supabase_not_connected'
  if (data.status === 'protected' && userActivated) return 'protected'
  return 'firewall_off'
}

// ── Small components ──────────────────────────────────────────────────────────

interface MetricCardProps {
  icon: string
  value: string
  label: string
  sublabel?: string
  accent?: 'green' | 'yellow' | 'red' | 'blue'
}

function MetricCard({ icon, value, label, sublabel, accent }: MetricCardProps) {
  const accentMap = {
    green:  'border-green-700 bg-green-950',
    yellow: 'border-yellow-700 bg-yellow-950',
    red:    'border-red-700 bg-red-950',
    blue:   'border-blue-700 bg-blue-950',
  }
  const cls = accent ? accentMap[accent] : 'border-slate-700 bg-slate-800'
  return (
    <article
      className={`${cls} border rounded-2xl p-6 md:p-7 flex flex-col items-center gap-3 shadow-xl`}
      aria-label={`${label}: ${value}`}
    >
      <span className="text-4xl select-none" aria-hidden="true">{icon}</span>
      <strong
        className="text-4xl md:text-5xl font-black text-white tabular-nums leading-none"
        aria-live="polite"
        aria-atomic="true"
      >
        {value}
      </strong>
      <span className="text-sm md:text-base text-slate-300 font-semibold text-center" aria-hidden="true">
        {label}
      </span>
      {sublabel !== undefined && (
        <span className="text-xs text-slate-500 text-center">{sublabel}</span>
      )}
    </article>
  )
}

interface SimStepRowProps {
  step: SimStep
  visible: boolean
  isLast: boolean
}

function SimStepRow({ step, visible, isLast }: SimStepRowProps) {
  const variantCls = {
    neutral: 'border-slate-600 bg-slate-800',
    info:    'border-blue-700 bg-blue-950',
    success: 'border-green-600 bg-green-950',
    error:   'border-red-600 bg-red-950',
  }[step.variant]

  const textCls = {
    neutral: 'text-slate-200',
    info:    'text-blue-200',
    success: 'text-green-200',
    error:   'text-red-200',
  }[step.variant]

  const detailCls = {
    neutral: 'text-slate-400',
    info:    'text-blue-400',
    success: 'text-green-400',
    error:   'text-red-400',
  }[step.variant]

  return (
    <div className={`flex flex-col items-center gap-0 transition-all duration-500 ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}>
      <div className={`${variantCls} border rounded-xl px-5 py-3 flex items-center gap-3 w-full max-w-sm`}>
        <span className="text-2xl select-none shrink-0" aria-hidden="true">{step.icon}</span>
        <div className="min-w-0">
          <p className={`font-bold text-sm md:text-base ${textCls}`}>{step.label}</p>
          <p className={`text-xs truncate ${detailCls}`}>{step.detail}</p>
        </div>
      </div>
      {!isLast && (
        <div className={`w-0.5 h-5 ${visible ? 'bg-slate-500' : 'bg-transparent'} transition-colors duration-300`} aria-hidden="true" />
      )}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [shieldStatus, setShieldStatus]     = useState<ShieldStatus>('loading')
  const [activated, setActivated]           = useState(false)
  const [metrics, setMetrics]               = useState<DashboardMetrics>(
    IS_DEMO_MODE ? DEMO_METRICS : ZERO_METRICS,
  )
  const [lastData, setLastData]             = useState<AggregatorStatusResponse | null>(null)
  const [lastUsageCheck, setLastUsageCheck] = useState<CheckUsageResponse | null>(null)
  const [version, setVersion]               = useState('1.0.0')
  const [lastUpdated, setLastUpdated]       = useState<Date | null>(null)
  const [actionLoading, setActionLoading]   = useState(false)
  const [message, setMessage]               = useState<string | null>(null)
  const [backendError, setBackendError]     = useState<string | null>(null)

  // ── Simulation state ───────────────────────────────────────────────────────
  const [simActive, setSimActive]   = useState(false)
  const [simStep, setSimStep]       = useState(0)
  const [simBlocked, setSimBlocked] = useState(false)

  const simSteps = getSimSteps(simBlocked)

  // Self-advancing simulation: each step appears SIM_STEP_MS after the previous
  useEffect(() => {
    if (!simActive) return
    if (simStep >= simSteps.length) {
      setSimActive(false)
      return
    }
    const timer = setTimeout(() => setSimStep(s => s + 1), SIM_STEP_MS)
    return () => clearTimeout(timer)
  }, [simActive, simStep, simSteps.length])

  const startSimulation = useCallback((blocked: boolean) => {
    setSimBlocked(blocked)
    setSimStep(0)
    setSimActive(true)
  }, [])

  const resetSimulation = useCallback(() => {
    setSimActive(false)
    setSimStep(0)
  }, [])

  // ── Poll /v1/aggregator/status ─────────────────────────────────────────────
  const refreshStatus = useCallback(async () => {
    try {
      const data = await fetchAggregatorStatus()
      setLastData(data)
      setVersion(data.version)
      setBackendError(null)
      if (!IS_DEMO_MODE) setMetrics(metricsFromStatus(data, lastUsageCheck))
      setShieldStatus(systemStatusToShield(true, data, activated))
    } catch {
      setBackendError(`Cannot reach backend at ${API_BASE}`)
      setLastData(null)
      setActivated(false)
      setShieldStatus('firewall_off')
    } finally {
      setLastUpdated(new Date())
    }
  }, [activated, lastUsageCheck])

  useEffect(() => {
    void refreshStatus()
    const id = setInterval(() => void refreshStatus(), STATUS_POLL_MS)
    return () => clearInterval(id)
  }, [refreshStatus])

  // ── Demo counter drift ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!IS_DEMO_MODE) return
    const id = setInterval(() => {
      setMetrics(prev => ({
        openrouterCallsBlocked: prev.openrouterCallsBlocked + Math.floor(Math.random() * 3),
        estimatedMoneySaved:    prev.estimatedMoneySaved    + Math.floor(Math.random() * 2),
        monthlyBudgetRemaining: Math.max(0, prev.monthlyBudgetRemaining - Math.random() * 0.01),
        currentTier:            prev.currentTier,
      }))
    }, DEMO_DRIFT_MS)
    return () => clearInterval(id)
  }, [])

  // ── Primary action ─────────────────────────────────────────────────────────
  const handleAction = useCallback(async () => {
    setActionLoading(true)
    setMessage(null)
    try {
      const resp = await checkUsage(DEMO_USER_UUID)
      if (resp.allowed) {
        setActivated(true)
        setLastUsageCheck(resp)
        if (!IS_DEMO_MODE) {
          setMetrics(prev => ({
            ...prev,
            monthlyBudgetRemaining: resp.remaining_budget_usd,
            currentTier: 'Pro',
          }))
        }
        setShieldStatus(lastData ? systemStatusToShield(true, lastData, true) : 'protected')
        setMessage('✅ Billing shield activated — all OpenRouter calls are now gated.')
      } else {
        const reasons: Record<string, string> = {
          supabase_unavailable:           'Supabase is not configured. Add SUPABASE_SERVICE_ROLE_KEY to your .env.',
          quota_not_found:                'User quota not provisioned. Run provision_user_quota() in Supabase.',
          monthly_message_limit_exceeded: 'Monthly message limit reached. Upgrade your plan.',
          monthly_budget_exceeded:        'Monthly budget reached. Upgrade your plan.',
        }
        setMessage(`⚠️ ${reasons[resp.reason] ?? `Activation blocked: ${resp.reason}`}`)
        setShieldStatus('firewall_off')
      }
    } catch {
      if (IS_DEMO_MODE) {
        setActivated(true)
        setShieldStatus('protected')
        setMessage('🛡️ Demo mode: billing shield activated locally (backend offline).')
      } else {
        setShieldStatus('firewall_off')
        setMessage(`⚠️ Cannot reach backend at ${API_BASE}. Ensure the gatekeeper service is running.`)
      }
    } finally {
      setActionLoading(false)
    }
  }, [activated, lastData, lastUsageCheck])

  // Suppress lint — activated is used in refreshStatus closure and handleAction
  void activated
  void lastUsageCheck

  // ── Derived state ──────────────────────────────────────────────────────────
  const isLoading   = shieldStatus === 'loading'
  const isProtected = shieldStatus === 'protected'
  const isSubabase  = shieldStatus === 'supabase_not_connected'

  const circuitState = lastData?.circuit_breaker_state ?? 'unknown'
  const circuitColor =
    circuitState === 'closed' ? 'text-green-400' :
    circuitState === 'open'   ? 'text-red-400'   : 'text-yellow-400'

  const demoMode = IS_DEMO_MODE || (lastData?.demo_mode ?? false)
  const orOk     = lastData?.openrouter_configured ?? false
  const polarOk  = lastData?.polar_configured ?? false

  const budgetDisplay = metrics.monthlyBudgetRemaining < 0
    ? '--'
    : `$${metrics.monthlyBudgetRemaining.toFixed(2)}`

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-slate-950 text-white flex flex-col font-sans antialiased">

      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50
                   focus:bg-white focus:text-slate-950 focus:px-5 focus:py-3
                   focus:rounded-xl focus:text-lg focus:font-bold focus:shadow-2xl"
      >
        Skip to main content
      </a>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800 shrink-0" role="banner">
        <div className="flex flex-col">
          <div className="flex items-center gap-3">
            <span className="text-2xl select-none" aria-hidden="true">🛡️</span>
            <span className="text-lg font-bold tracking-tight">Aivora Gatekeeper</span>
          </div>
          <span className="text-xs text-slate-400 ml-9 -mt-0.5 font-medium">AI Aggregator Billing Firewall</span>
        </div>

        <div className="flex items-center gap-3 text-xs">
          {demoMode && (
            <span className="bg-yellow-950 text-yellow-300 border border-yellow-700 px-2 py-1 rounded-full font-semibold"
              role="status" aria-label="Demo mode active">Demo mode</span>
          )}
          {!orOk && !demoMode && (
            <span className="bg-orange-950 text-orange-300 border border-orange-700 px-2 py-1 rounded-full font-semibold"
              role="status">OpenRouter not set</span>
          )}
          {!polarOk && !demoMode && (
            <span className="bg-slate-800 text-slate-400 border border-slate-600 px-2 py-1 rounded-full font-semibold"
              role="status">Polar not set</span>
          )}
          {backendError !== null && !IS_DEMO_MODE && (
            <span className="bg-red-950 text-red-300 border border-red-700 px-2 py-1 rounded-full font-semibold"
              role="alert">Backend offline</span>
          )}
          {lastUpdated !== null && (
            <time dateTime={lastUpdated.toISOString()} className="hidden sm:block text-slate-500">
              {lastUpdated.toLocaleTimeString()}
            </time>
          )}
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────────────────────────── */}
      <main id="main-content" className="flex-1 flex flex-col items-center gap-10 md:gap-12 px-4 sm:px-6 py-10 max-w-5xl mx-auto w-full">

        {/* ════════════════════════════════════════════════════════════════════
            HERO — Status indicator + tagline
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-live="polite" aria-label="Billing firewall status"
          className="flex flex-col items-center gap-4 text-center w-full">

          {isLoading ? (
            <>
              <span className="text-8xl md:text-9xl select-none animate-pulse" aria-hidden="true">⏳</span>
              <h1 className="text-4xl md:text-5xl font-black tracking-widest text-slate-400 uppercase">Checking…</h1>
              <p className="text-base text-slate-500 font-semibold">Contacting gatekeeper</p>
            </>
          ) : isProtected ? (
            <>
              <span className="text-8xl md:text-9xl select-none drop-shadow-[0_0_48px_rgba(34,197,94,0.75)]" aria-hidden="true">🟢</span>
              <h1 className="text-5xl md:text-6xl font-black tracking-widest text-green-400 uppercase" aria-label="Status: Protected">PROTECTED</h1>
              <p className="text-xl text-green-300 font-bold">Billing Firewall Active</p>
            </>
          ) : isSubabase ? (
            <>
              <span className="text-7xl md:text-8xl select-none animate-pulse" aria-hidden="true">🟠</span>
              <h1 className="text-3xl md:text-4xl font-black tracking-widest text-orange-400 uppercase" aria-label="Status: Supabase not connected">SUPABASE NOT CONNECTED</h1>
              <p className="text-base text-orange-300 font-semibold">Set SUPABASE_SERVICE_ROLE_KEY in .env</p>
            </>
          ) : (
            <>
              <span className="text-8xl md:text-9xl select-none animate-pulse" aria-hidden="true">🔴</span>
              <h1 className="text-5xl md:text-6xl font-black tracking-widest text-red-400 uppercase" aria-label="Status: Billing Firewall Off">BILLING FIREWALL OFF</h1>
              <p className="text-xl text-red-300 font-bold">Bill at Risk</p>
            </>
          )}

          {/* Hero description — always visible */}
          <p className="text-slate-400 text-sm md:text-base max-w-xl mt-1 leading-relaxed">
            Prevent quota abuse, API overspending, and OpenRouter billing surprises
            before requests reach your models.
          </p>
        </section>

        {/* ── Feedback / error banner ─────────────────────────────────────── */}
        {message !== null && (
          <div role="alert" aria-live="assertive"
            className="w-full max-w-lg text-center bg-slate-800 border border-slate-600
                       rounded-2xl px-8 py-4 text-base text-white font-medium shadow-xl">
            {message}
          </div>
        )}
        {backendError !== null && message === null && !IS_DEMO_MODE && (
          <div role="alert"
            className="w-full max-w-lg text-center bg-red-950 border border-red-700 rounded-2xl px-8 py-4 text-sm text-red-200 shadow-xl">
            <p className="font-bold text-base mb-1">Backend unreachable</p>
            <p>{backendError}</p>
          </div>
        )}

        {/* ── Primary action button ────────────────────────────────────────── */}
        <button
          type="button"
          onClick={() => void handleAction()}
          disabled={actionLoading || isLoading}
          aria-label={actionLoading ? 'Processing' : isProtected ? 'Billing shield active — rotate keys' : 'Activate billing shield'}
          aria-busy={actionLoading}
          className={[
            'w-full max-w-sm sm:max-w-md min-h-20 px-10 py-6 rounded-2xl',
            'text-xl md:text-2xl font-black uppercase tracking-wide text-white',
            'transition-all duration-200 focus:outline-none focus:ring-4',
            'focus:ring-offset-4 focus:ring-offset-slate-950',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            isProtected
              ? 'bg-blue-700 hover:bg-blue-600 focus:ring-blue-500 shadow-[0_0_36px_rgba(59,130,246,0.4)] hover:shadow-[0_0_52px_rgba(59,130,246,0.6)]'
              : 'bg-green-700 hover:bg-green-600 focus:ring-green-500 shadow-[0_0_36px_rgba(34,197,94,0.4)] hover:shadow-[0_0_52px_rgba(34,197,94,0.6)]',
          ].join(' ')}
        >
          {actionLoading ? '⏳ Processing…' : isProtected ? '🔑 Rotate API Keys' : '🛡️ Activate Billing Shield'}
        </button>

        {/* ════════════════════════════════════════════════════════════════════
            LIVE DEMO CARDS
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-label="Live billing firewall metrics"
          className="w-full grid grid-cols-2 lg:grid-cols-4 gap-4 md:gap-5">
          <MetricCard
            icon="🚫"
            value={metrics.openrouterCallsBlocked.toLocaleString()}
            label="Calls Blocked"
            sublabel="OpenRouter"
            accent={metrics.openrouterCallsBlocked > 0 ? 'red' : undefined}
          />
          <MetricCard
            icon="💰"
            value={`$${metrics.estimatedMoneySaved.toLocaleString()}`}
            label="Money Saved"
            sublabel="Estimated"
            accent={metrics.estimatedMoneySaved > 0 ? 'green' : undefined}
          />
          <MetricCard
            icon="📊"
            value={budgetDisplay}
            label="Budget Remaining"
            sublabel="This month"
            accent={metrics.monthlyBudgetRemaining >= 0 ? 'blue' : undefined}
          />
          <MetricCard
            icon="📋"
            value={metrics.currentTier}
            label="Current Tier"
            sublabel="Subscription"
          />
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            AI REQUEST SIMULATION
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-label="AI request simulation"
          className="w-full bg-slate-900 border border-slate-700 rounded-2xl p-6 md:p-8">

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
            <div>
              <h2 className="text-xl md:text-2xl font-black text-white tracking-tight">
                AI Request Simulation
              </h2>
              <p className="text-slate-400 text-sm mt-1">
                Watch a request flow through the billing firewall in real time.
              </p>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                onClick={() => startSimulation(false)}
                disabled={simActive}
                aria-label="Simulate an allowed request"
                className="px-4 py-2 rounded-xl bg-green-700 hover:bg-green-600 text-white text-sm font-bold
                           disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-green-500"
              >
                ▶ Allowed
              </button>
              <button
                type="button"
                onClick={() => startSimulation(true)}
                disabled={simActive}
                aria-label="Simulate a blocked request"
                className="px-4 py-2 rounded-xl bg-red-700 hover:bg-red-600 text-white text-sm font-bold
                           disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-red-500"
              >
                ▶ Blocked
              </button>
              {simStep > 0 && !simActive && (
                <button
                  type="button"
                  onClick={resetSimulation}
                  aria-label="Reset simulation"
                  className="px-4 py-2 rounded-xl bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm font-bold
                             transition-colors focus:outline-none focus:ring-2 focus:ring-slate-500"
                >
                  ↺ Reset
                </button>
              )}
            </div>
          </div>

          {/* Step list */}
          <div
            role="log"
            aria-label="Simulation steps"
            aria-live="polite"
            className="flex flex-col items-center"
          >
            {simStep === 0 && !simActive ? (
              <p className="text-slate-500 text-sm py-8 text-center">
                Click <strong className="text-green-400">▶ Allowed</strong> or{' '}
                <strong className="text-red-400">▶ Blocked</strong> to run the simulation.
              </p>
            ) : (
              simSteps.map((step, i) => (
                <SimStepRow
                  key={step.label}
                  step={step}
                  visible={simStep > i}
                  isLast={i === simSteps.length - 1}
                />
              ))
            )}
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            WHY AI AGGREGATORS NEED THIS
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-label="Why AI aggregators need a billing firewall"
          className="w-full">

          <h2 className="text-xl md:text-2xl font-black text-white tracking-tight mb-5">
            Why AI Aggregators Need This
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">

            {/* WITHOUT */}
            <div className="bg-red-950 border border-red-800 rounded-2xl p-5 md:p-6"
              aria-label="Without Gatekeeper">
              <h3 className="text-red-300 font-black text-base mb-4 uppercase tracking-wider">
                ❌ Without Gatekeeper
              </h3>
              <div className="flex flex-col gap-3">
                {[
                  { icon: '👤', label: 'User sends request' },
                  { icon: '↓',  label: '' },
                  { icon: '🤖', label: 'OpenRouter called directly' },
                  { icon: '↓',  label: '' },
                  { icon: '💸', label: 'Surprise bill — no visibility' },
                ].map(({ icon, label }, i) =>
                  icon === '↓' ? (
                    <div key={i} className="flex justify-start pl-4">
                      <span className="text-red-600 text-lg font-bold" aria-hidden="true">↓</span>
                    </div>
                  ) : (
                    <div key={i} className="flex items-center gap-3 bg-red-900 border border-red-700 rounded-xl px-4 py-2">
                      <span className="text-xl" aria-hidden="true">{icon}</span>
                      <span className="text-red-200 font-semibold text-sm">{label}</span>
                    </div>
                  )
                )}
                <p className="text-red-400 text-xs mt-2">
                  10 000 spam requests × $0.018 = <strong className="text-red-300">$180 unplanned spend</strong>
                </p>
              </div>
            </div>

            {/* WITH */}
            <div className="bg-green-950 border border-green-800 rounded-2xl p-5 md:p-6"
              aria-label="With Gatekeeper">
              <h3 className="text-green-300 font-black text-base mb-4 uppercase tracking-wider">
                ✅ With Gatekeeper
              </h3>
              <div className="flex flex-col gap-3">
                {[
                  { icon: '👤', label: 'User sends request' },
                  { icon: '↓',  label: '' },
                  { icon: '🛡️', label: 'Gatekeeper — quota + cost check' },
                  { icon: '↓',  label: '' },
                  { icon: '✅', label: 'Allowed → OpenRouter called' },
                  { icon: '↓',  label: '' },
                  { icon: '💰', label: 'Controlled costs — zero surprises' },
                ].map(({ icon, label }, i) =>
                  icon === '↓' ? (
                    <div key={i} className="flex justify-start pl-4">
                      <span className="text-green-600 text-lg font-bold" aria-hidden="true">↓</span>
                    </div>
                  ) : (
                    <div key={i} className="flex items-center gap-3 bg-green-900 border border-green-700 rounded-xl px-4 py-2">
                      <span className="text-xl" aria-hidden="true">{icon}</span>
                      <span className="text-green-200 font-semibold text-sm">{label}</span>
                    </div>
                  )
                )}
                <p className="text-green-400 text-xs mt-2">
                  $500 monthly budget cap = <strong className="text-green-300">$0 overrun guaranteed</strong>
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            POTENTIAL SAVINGS PROOF CARD
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-label="Potential savings calculator"
          className="w-full bg-gradient-to-br from-slate-900 to-slate-800 border border-slate-600 rounded-2xl p-6 md:p-8">

          <div className="flex items-start gap-4">
            <span className="text-4xl shrink-0 select-none" aria-hidden="true">💡</span>
            <div className="flex-1 min-w-0">
              <h2 className="text-xl md:text-2xl font-black text-white tracking-tight mb-4">
                Potential Savings
              </h2>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-5">
                {[
                  { calc: '10 000 spam requests', result: '× $0.018 avg', total: '= $180 saved', color: 'text-green-300' },
                  { calc: '100 000 requests/mo', result: '× $0.018 avg', total: '= $1 800 saved', color: 'text-yellow-300' },
                  { calc: '1 000 000 requests/mo', result: '× $0.018 avg', total: '= $18 000 saved', color: 'text-orange-300' },
                ].map(({ calc, result, total, color }) => (
                  <div key={calc}
                    className="bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-sm">
                    <p className="text-slate-300 font-semibold mb-1">{calc}</p>
                    <p className="text-slate-500">{result}</p>
                    <p className={`font-black text-base mt-1 ${color}`}>{total}</p>
                  </div>
                ))}
              </div>

              <div className="flex flex-col sm:flex-row gap-3">
                <div className="flex-1 bg-slate-900 border border-green-800 rounded-xl px-4 py-3 text-sm">
                  <p className="text-green-300 font-bold mb-1">Enterprise plan ($299/mo)</p>
                  <p className="text-slate-400">$500 hard monthly budget cap</p>
                  <p className="text-slate-400">Unlimited messages, 100 tokens/sec refill</p>
                  <p className="text-green-400 font-semibold mt-1">→ Zero surprise bills, ever.</p>
                </div>
                <div className="flex-1 bg-slate-900 border border-blue-800 rounded-xl px-4 py-3 text-sm">
                  <p className="text-blue-300 font-bold mb-1">Free tier ($0/mo)</p>
                  <p className="text-slate-400">50 messages/month limit</p>
                  <p className="text-slate-400">$0.50 budget cap enforced by DB</p>
                  <p className="text-blue-400 font-semibold mt-1">→ Safe to expose to new users.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ════════════════════════════════════════════════════════════════════
            BUILT FOR NEXT.JS + SUPABASE
        ════════════════════════════════════════════════════════════════════ */}
        <section aria-label="Next.js integration guide"
          className="w-full bg-slate-900 border border-slate-700 rounded-2xl p-6 md:p-8">

          <h2 className="text-xl md:text-2xl font-black text-white tracking-tight mb-2">
            Built for Next.js + Supabase AI Aggregators
          </h2>
          <p className="text-slate-400 text-sm md:text-base mb-6">
            Drop-in billing firewall. Every OpenRouter call is quota-checked, costed,
            and logged before reaching the model.
          </p>

          {/* Integration checklist */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            {[
              { ok: lastData?.supabase_available ?? false, label: 'Supabase connected',    hint: 'Set SUPABASE_SERVICE_ROLE_KEY' },
              { ok: orOk,                                  label: 'OpenRouter configured', hint: 'Set OPENROUTER_API_KEY' },
              { ok: polarOk,                               label: 'Polar.sh webhooks',     hint: 'Set POLAR_WEBHOOK_SECRET' },
              { ok: isProtected,                           label: 'Billing shield active', hint: 'Click Activate Billing Shield' },
            ].map(({ ok, label, hint }) => (
              <div key={label}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl border
                  ${ok ? 'bg-green-950 border-green-700' : 'bg-slate-800 border-slate-700'}`}
                aria-label={`${label}: ${ok ? 'complete' : hint}`}
              >
                <span aria-hidden="true">{ok ? '✅' : '⬜'}</span>
                <div>
                  <p className={`font-semibold ${ok ? 'text-green-300' : 'text-slate-300'}`}>{label}</p>
                  {!ok && <p className="text-slate-500 text-xs">{hint}</p>}
                </div>
              </div>
            ))}
          </div>
        </section>

      </main>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer
        className="flex items-center justify-between px-6 py-4 border-t border-slate-800 text-xs text-slate-500 shrink-0"
        role="contentinfo"
      >
        <span>
          Circuit Breaker:{' '}
          <span className={`${circuitColor} font-semibold`} aria-label={`Circuit breaker: ${circuitState}`}>
            {circuitState.toUpperCase()}
          </span>
        </span>
        <span>Aivora Gatekeeper v{version}</span>
      </footer>
    </div>
  )
}
