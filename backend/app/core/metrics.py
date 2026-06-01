"""
Prometheus metrics registry for Aivora Gatekeeper.

All metric objects are module-level singletons created at import time.
Import this module once from main.py; all other modules import individual
counters/histograms as needed.

Naming follows the Prometheus convention: <namespace>_<subsystem>_<name>_<unit>
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Quota decisions (GatewayMiddleware) ───────────────────────────────────────

QUOTA_DECISIONS = Counter(
    "gatekeeper_quota_decisions_total",
    "Total quota decisions made by GatewayMiddleware.",
    ["result"],           # "allowed" | "denied"
)

QUOTA_DENY_REASONS = Counter(
    "gatekeeper_quota_deny_reasons_total",
    "Quota denial reasons from GatewayMiddleware.",
    ["reason"],           # RATE_LIMIT_EXCEEDED, MONTHLY_BUDGET_EXCEEDED, …
)

QUOTA_LATENCY = Histogram(
    "gatekeeper_quota_latency_seconds",
    "End-to-end latency of the Supabase quota RPC call (seconds).",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ── Circuit breaker ───────────────────────────────────────────────────────────

CIRCUIT_BREAKER_OPEN = Gauge(
    "gatekeeper_circuit_breaker_open",
    "1 when the circuit breaker is OPEN (Supabase unreachable), 0 otherwise.",
)

CIRCUIT_BREAKER_FALLBACKS = Counter(
    "gatekeeper_circuit_breaker_fallbacks_total",
    "Total requests served by the circuit-breaker fallback (not via Supabase).",
)

# ── In-memory cache ───────────────────────────────────────────────────────────

CACHE_HITS = Counter(
    "gatekeeper_cache_hits_total",
    "In-memory quota cache hits (fast-reject or fast-allow).",
)

CACHE_MISSES = Counter(
    "gatekeeper_cache_misses_total",
    "In-memory quota cache misses (requires Supabase RPC).",
)

# ── Aggregator (proxy layer) ──────────────────────────────────────────────────

PROXY_REQUESTS = Counter(
    "gatekeeper_proxy_requests_total",
    "Requests received by the OpenRouter proxy endpoint.",
    ["outcome"],          # "allowed" | "quota_denied" | "openrouter_error"
)

WORKSPACE_BUDGET_BLOCKS = Counter(
    "gatekeeper_workspace_budget_blocks_total",
    "Requests blocked at the workspace budget gate.",
)
