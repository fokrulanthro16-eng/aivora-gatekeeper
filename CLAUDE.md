# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (run from `backend/`)

```powershell
# Activate venv (Windows)
.venv\Scripts\Activate.ps1

# Dev server (auto-reload)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Tests
pytest tests/ -v
pytest tests/test_foo.py::test_bar -v   # single test

# Type checking
mypy app/

# Linting / formatting
ruff check app/
ruff format app/

# Syntax check all modules
python -m compileall app
```

### Frontend (run from `frontend/`)

```powershell
npm run dev       # Vite dev server on :5173
npm run build     # tsc -b && vite build
npm run lint      # eslint
```

## Architecture

### Monorepo layout

```
backend/   — FastAPI app (Python, .venv inside)
frontend/  — React 19 + Vite + Tailwind CSS 4
database/  — SQL migration files only (apply to Supabase manually)
```

### Backend request flow

```
Incoming HTTP
  → CORSMiddleware
  → GatewayMiddleware        (app/middleware/gateway.py)
      validates X-User-UUID, estimates token cost, calls Supabase RPC
      via CircuitBreaker, caches decisions, attaches X-RateLimit-* headers
  → Route handlers
      /health, /v1/gatekeeper/*   (app/routes/gatekeeper.py)
      /v1/aggregator/*, /v1/webhooks/polar  (app/routes/aggregator.py)
```

All routes listed in `Settings.GATEWAY_BYPASS_PATHS` skip `GatewayMiddleware` entirely. Every `/v1/gatekeeper/*` and `/v1/aggregator/*` route is in this bypass list because those handlers perform their own quota logic directly.

### Two route groups

| Router | Purpose |
|--------|---------|
| `gatekeeper` | Original token-bucket proxy oracle: `GET /health`, `GET /v1/gatekeeper/status`, `POST /v1/gatekeeper/protect` (deducts tokens), `POST /v1/gatekeeper/simulate-request` (read-only) |
| `aggregator` | AI-provider proxy + billing: `GET /v1/aggregator/status`, `POST /v1/aggregator/check-usage`, `POST /v1/aggregator/proxy-openrouter`, `POST /v1/webhooks/polar` |

### Key singletons (module-level, created on first call)

- `get_quota_cache()` — `InMemoryCache` with TTL + LRU eviction (mirrors Redis API for drop-in swap)
- `get_circuit_breaker()` — `CircuitBreaker` wrapping all Supabase RPC calls; CLOSED→OPEN→HALF_OPEN
- `get_supabase_client()` — async Supabase client, initialised in `lifespan`; `is_supabase_available()` returns False if init failed
- `get_settings()` — `@lru_cache` Pydantic settings, reads from `.env`

### Quota decision path

`GatewayMiddleware` and `/protect` call `process_token_bucket()` in `supabase_client.py`, which calls the `process_token_bucket_leak` Supabase RPC through the circuit breaker.

`/v1/aggregator/check-usage` and `/proxy-openrouter` call `check_ai_usage()` in `services/usage.py`, which calls the `check_and_consume_ai_usage` Supabase RPC through the same circuit breaker.

When the circuit breaker is OPEN: `DEMO_MODE=true` → fail-open (allowed); `DEMO_MODE=false` → fail-closed (rejected).

### Cost estimation

`app/utils/token_estimator.py` — character-ratio heuristic for token counts (handles OpenAI messages, Anthropic messages, legacy completions).

`app/utils/cost_estimator.py` — USD cost lookup table (`PROVIDER_COSTS`) mirroring `database/migrations/008_provider_costs.sql`. Used by the aggregator routes before forwarding to OpenRouter.

### Frontend

Single-page React app. Polls `GET /v1/aggregator/status` every 30 s. Primary action (`Activate Billing Shield`) calls `POST /v1/aggregator/check-usage`.

Environment variables (set in `frontend/.env`):
- `VITE_API_BASE_URL` — backend URL (falls back to `VITE_API_URL`, then `http://localhost:8000`)
- `VITE_DEMO_MODE=true` — enables animated demo counters without a real backend

### Database

Migrations in `database/migrations/` are applied manually to Supabase. Key tables: `billing_tiers`, `user_quotas`, `subscriptions`, `polar_webhook_events`, `provider_costs`. Key RPCs: `process_token_bucket_leak`, `check_and_consume_ai_usage`.

## Environment setup

Copy `backend/.env.example` → `backend/.env` and set at minimum:
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`
- `OPENROUTER_API_KEY` (for proxy-openrouter)
- `POLAR_WEBHOOK_SECRET` + `POLAR_ACCESS_TOKEN` (for Polar webhooks)

Set `DEMO_MODE=true` to run without a live Supabase instance (all quota checks pass).
