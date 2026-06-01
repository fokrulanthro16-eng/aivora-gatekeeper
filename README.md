# Aivora Gatekeeper Engine — AI Aggregator Billing Firewall

A production-grade billing firewall and quota engine for AI aggregator platforms.  
Sits between your users and OpenRouter (or any LLM provider), enforcing per-user subscription limits, dollar budgets, and token-bucket rate limits — with a single-screen Grandma Theory dashboard.

---

## Built for Reddit Client Requirement: OpenRouter Usage Counter & Limits

This engine directly solves the canonical AI aggregator billing problem:

> *"I'm building a Next.js + Supabase SaaS that proxies user requests to OpenRouter.  
> I need per-user message limits, monthly dollar budgets, and subscription tier enforcement  
> — all enforced atomically before each OpenRouter call."*

### Next.js Frontend Integration

Call `/v1/aggregator/check-usage` from your Next.js API route before forwarding to OpenRouter:

```typescript
// app/api/chat/route.ts
const gate = await fetch('https://your-gatekeeper/v1/aggregator/check-usage', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    user_uuid:        session.user.id,   // from Supabase auth
    provider:         'openai',
    model:            'gpt-4o-mini',
    estimated_tokens: 500,
    estimated_cost:   0.0001,
  }),
})
const { allowed, reason, remaining_messages, remaining_budget_usd } = await gate.json()
if (!allowed) return NextResponse.json({ error: reason }, { status: 429 })

// Safe to call OpenRouter now
const response = await openai.chat.completions.create({ ... })
```

Or use the full proxy endpoint that does both the quota check AND the OpenRouter call in one request:

```typescript
const response = await fetch('https://your-gatekeeper/v1/aggregator/proxy-openrouter', {
  method: 'POST',
  body: JSON.stringify({
    user_uuid: session.user.id,
    model:     'openai/gpt-4o-mini',
    messages:  [{ role: 'user', content: userMessage }],
    max_tokens: 1024,
  }),
})
```

### Supabase RPC Quota Enforcement

The core quota check is a single PL/pgSQL function (`check_and_consume_ai_usage`) that:

1. **Locks** the user's quota row with `SELECT … FOR UPDATE` — serialises concurrent requests per user.
2. **Checks** active subscription tier (from Polar.sh) or falls back to the default tier.
3. **Enforces** monthly message limit (e.g. 50 / 1 000 / unlimited).
4. **Enforces** monthly dollar budget (e.g. $0.50 / $20 / $500).
5. **Enforces** token-bucket rate limit (burst ceiling + continuous refill rate).
6. **Deducts** from all three counters atomically in a single transaction.
7. **Logs** to `api_logs` for billing audit trail.
8. **Returns** `{ allowed, reason, remaining_messages, remaining_budget_usd, ... }`.

### Polar.sh Subscription Tier Sync

POST `/v1/webhooks/polar` to receive Polar lifecycle events. The handler:

- Verifies the HMAC-SHA256 webhook signature.
- Persists the raw event to `polar_webhook_events` for idempotent replay.
- Upserts into `subscriptions` and updates `user_quotas.billing_tier_id`.

Configure in Polar Dashboard → Webhooks → add your endpoint URL.  
Map your Polar product names to tiers in `backend/app/services/polar.py → POLAR_TIER_MAP`.

### OpenRouter Pre-Call Billing Protection

The `proxy-openrouter` endpoint flow:

```
User Request → Cost Estimate → check_and_consume_ai_usage() → Block / Allow → OpenRouter
                                         ↑
                              SELECT … FOR UPDATE on user_quotas
                              + usage_counters row lock
                              atomic deduction on success
```

Cost is estimated BEFORE the call using the provider cost table (`008_provider_costs.sql`).  
If Supabase rejects the call for any reason, OpenRouter is **never called** — zero risk of unmetered spend.

### Real Production Requirements

| Component | Required | Purpose |
|---|---|---|
| Supabase project | Yes | Quota state, subscriptions, audit logs |
| Database migrations 001–011 | Yes | All tables + RPC function |
| `provision_user_quota()` | Yes | Called after user sign-up to create quota row |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service-role access to bypass RLS |
| `OPENROUTER_API_KEY` | Yes (for proxy) | Forward requests to LLM providers |
| `POLAR_WEBHOOK_SECRET` | Yes (for billing) | Verify Polar webhook signatures |
| `POLAR_ACCESS_TOKEN` | Optional | Outbound Polar API calls |

### Local Demo Commands

```bash
# Mode A: demo without Supabase (shows UI, no real quota enforcement)
cp .env.example .env
# Edit .env: DEMO_MODE=true, GATEWAY_FAIL_OPEN=true
cd backend && uvicorn app.main:app --reload --port 8000

cp frontend/.env.example frontend/.env
# Edit frontend/.env: VITE_DEMO_MODE=true
cd frontend && npm run dev

# Mode B: production with real Supabase
cp .env.example .env
# Edit .env: fill SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY
# Apply migrations 001–011
# Provision test user: SELECT provision_user_quota('<uuid>'::uuid, 1);
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

---

## What Is the Aivora Gatekeeper Engine?

Every AI product that calls an LLM API faces the same three threats:

| Threat | Impact |
|---|---|
| **Runaway loops / bugs** | A single misbehaving client empties the monthly budget in minutes |
| **Prompt-injection & spam** | Malicious users extract value at your cost |
| **Provider outages** | One unavailable upstream silently breaks your entire product |

The Gatekeeper Engine solves all three in a single, self-contained service:

1. **Token bucket quota** — every user gets a bucket that refills continuously. The bucket drains on each request. When it empties, requests are rejected with a structured 429 until it refills. The algorithm is implemented as a concurrency-safe PL/pgSQL function in PostgreSQL, so no external locking service (Redis, ZooKeeper) is needed.

2. **Circuit breaker** — if Supabase becomes unreachable (5xx, timeout, DNS failure), the breaker trips to OPEN state and the gateway either fails-open (allows through, degraded mode) or fails-closed (rejects), depending on configuration. After a recovery window it probes with HALF_OPEN logic before fully re-closing.

3. **Grandma Theory dashboard** — a single-screen React UI shows one giant status indicator (🔴 STOPPED / 🟢 PROTECTED) and one button. A non-technical person can understand the system's health at a glance and act on it without reading a manual.

---

## Architecture

```
  Browser
    │
    │  HTTPS
    ▼
┌──────────────────────────────────────────────────────────┐
│  React Dashboard  (Vite + Tailwind v4)                   │
│                                                          │
│  • Polls  GET /v1/gatekeeper/status  every 30 s          │
│  • Calls  POST /v1/gatekeeper/protect  on button press   │
│  • Falls back to demo data when backend is unreachable   │
└──────────────────────┬───────────────────────────────────┘
                       │  HTTP  (port 8000)
                       ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI Gatekeeper  (Python 3.12 + uvicorn)             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  CORSMiddleware                                    │  │
│  └────────────────────┬───────────────────────────────┘  │
│                       │                                  │
│  ┌────────────────────▼───────────────────────────────┐  │
│  │  GatewayMiddleware  (quota enforcement)            │  │
│  │                                                    │  │
│  │  1. Validate X-User-UUID header                    │  │
│  │  2. Parse body → estimate token cost               │  │
│  │  3. InMemoryCache lookup (fast-reject for blocks)  │  │
│  │  4. CircuitBreaker.call(supabase_rpc, fallback)    │  │
│  │     ├─ CLOSED/HALF_OPEN → Supabase RPC             │  │
│  │     └─ OPEN             → degraded-mode response   │  │
│  │  5. Attach X-RateLimit-* headers                   │  │
│  └────────────────────┬───────────────────────────────┘  │
│                       │                                  │
│  ┌────────────────────▼───────────────────────────────┐  │
│  │  Route handlers                                    │  │
│  │    GET  /health                                    │  │
│  │    GET  /v1/gatekeeper/status                      │  │
│  │    POST /v1/gatekeeper/protect                     │  │
│  │    POST /v1/gatekeeper/simulate-request            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │  Supabase RPC
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Supabase  (PostgreSQL 15 + PostgREST)                   │
│                                                          │
│  process_token_bucket_leak(user_uuid, request_cost)      │
│    ├─ SELECT … FOR UPDATE  (row-level lock)              │
│    ├─ refill tokens: MIN(current + elapsed×rate, max)    │
│    ├─ check monthly budget hard ceiling                  │
│    ├─ deduct tokens atomically                           │
│    ├─ INSERT INTO api_logs                               │
│    └─ RETURN { allowed, remaining_tokens, reason }       │
│                                                          │
│  Tables:  billing_tiers · user_quotas · api_logs         │
└──────────────────────────────────────────────────────────┘
```

---

## Token Bucket Rate Limiting

The Gatekeeper uses a **continuous-refill token bucket** per user:

```
  Bucket level
      ▲
      │ max_tokens ─────────────────────────────────────────
      │                      ╭───╮         ╭──────────
      │          ╭───────────╯   ╰─────────╯
      │──────────╯
      └───────────────────────────────────────────────────▶ time
                   refills at rate tokens/sec, capped at max
```

**How a request is processed** (inside the `process_token_bucket_leak` PL/pgSQL function):

1. `SELECT … FOR UPDATE` locks the user's `user_quotas` row — serialises concurrent requests for the same user at the DB level without Redis.
2. Compute elapsed seconds since `last_refill_at`, add `elapsed × refill_rate` tokens, cap at `max_tokens`.
3. If the calendar month has rolled, reset `tokens_used_this_period` to zero.
4. Check the hard monthly budget cap (`tokens_used_this_period + cost > monthly_token_budget`).
5. If `current_tokens >= request_cost`: deduct, mark allowed.  Otherwise: reject, leave bucket unchanged.
6. `INSERT` one row into `api_logs` (immutable audit trail, regardless of outcome).
7. Return `{ "allowed": bool, "remaining_tokens": float, "reason": string }`.

**Billing tiers** (seeded at deploy time):

| Tier | Bucket Capacity | Refill Rate | Monthly Budget | Price |
|---|---|---|---|---|
| Free | 10,000 tokens | 1 token/sec | 500,000 | $0 |
| Pro | 100,000 tokens | 10 tokens/sec | 10,000,000 | $29/mo |
| Enterprise | 1,000,000 tokens | 100 tokens/sec | 500,000,000 | $299/mo |

For the full schema and migration commands see [`database/README.md`](database/README.md).

---

## Backend Middleware Flow

Every HTTP request to a non-bypass path passes through `GatewayMiddleware` in this order:

```
Request arrives
    │
    ▼
Is path in BYPASS_PATHS?  (/health, /docs, /v1/gatekeeper/*)
    ├─ YES → skip to route handler
    └─ NO  ↓

Validate X-User-UUID header
    ├─ missing → 401 MISSING_USER_IDENTITY
    ├─ invalid UUID → 401 INVALID_USER_IDENTITY
    └─ valid ↓

Read & cache request body (Starlette caches body in request._body,
so downstream handlers can re-read the same bytes)

Estimate token cost from body
    • OpenAI chat messages   → sum(char_count / chars_per_token) + max_tokens
    • OpenAI completions     → char_count(prompt) + max_tokens
    • Anthropic messages     → system + messages + max_tokens
    • Unknown / empty        → TOKEN_DEFAULT_COST (default: 10)
    • Hard ceiling           → TOKEN_MAX_COST (default: 10,000)

Check in-memory cache for recent negative decision
    ├─ blocked:{user_uuid} found (TTL 15 s) → 429 immediately (no DB call)
    └─ not found ↓

CircuitBreaker.call(supabase_rpc, fallback)
    ├─ CLOSED / HALF_OPEN → call Supabase RPC (5 s timeout)
    └─ OPEN               → fallback response (degraded mode)

Cache outcome
    • allowed  → cache quota:{user_uuid} = remaining_tokens (TTL 5 s)
    • rejected → cache blocked:{user_uuid} = reason (TTL 15 s)

Allowed?
    ├─ NO  → 429 with JSON error body + Retry-After header
    └─ YES → attach X-RateLimit-Remaining, X-RateLimit-Cost headers
             pass to route handler →  200 / route response
```

**Enterprise error responses** on rejection:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Token bucket exhausted. Retry after the bucket refills.",
    "details": {
      "reason": "insufficient_tokens",
      "requested_cost": 250,
      "remaining_tokens": 14
    }
  }
}
```

---

## Circuit Breaker Behaviour

The circuit breaker wraps every call to the Supabase RPC.  It trips on any exception (network error, timeout, HTTP 5xx) raised by the protected coroutine.

```
                    5 consecutive failures
          CLOSED ──────────────────────────────► OPEN
         (normal)                              (fallback only)
            ▲                                      │
            │                                      │ after CB_RECOVERY_TIMEOUT_SECONDS
            │                                      │ (default 60 s)
            │                                      ▼
            │         CB_HALF_OPEN_MAX_ATTEMPTS HALF_OPEN
            └─────────────── successes ─────── (probe request)
                                                   │
                                                   │ any failure
                                                   └──────────────► OPEN (re-trips)
```

| State | Behaviour |
|---|---|
| `CLOSED` | All calls pass through to Supabase. Failure count resets on any success. |
| `OPEN` | All calls immediately invoke the fallback. No Supabase calls are made. |
| `HALF_OPEN` | One probe request allowed per recovery window. Success increments the close counter; failure re-opens immediately. |

**Fallback behaviour** when breaker is OPEN (controlled by `DEMO_MODE` + `GATEWAY_FAIL_OPEN`):

| Mode | Fallback result |
|---|---|
| `DEMO_MODE=false` (production default) | `allowed=false`, reason `supabase_unavailable` — request rejected |
| `DEMO_MODE=true` | `allowed=true`, reason `circuit_open_degraded_mode` — request passes through |
| `GATEWAY_FAIL_OPEN=true` (explicit override) | same as demo mode — fail-open regardless of `DEMO_MODE` |

Current breaker state is visible at `GET /v1/gatekeeper/status`.

---

## Grandma Theory Frontend

> *"If your grandma can't tell whether the bill is safe in under three seconds, your dashboard is too complicated."*

The React dashboard enforces one rule: **one screen, one answer, one action**.

| Zone | Content |
|---|---|
| Status (top) | Giant 🔴 STOPPED (Bill at Risk) or 🟢 PROTECTED (Safe) indicator |
| Action (middle) | One button — either "Activate 1-Click Shield" or "Fix & Rotate API Keys" |
| Metrics (bottom) | Three oversized cards: Money Saved · Active Users · Blocked Spammers |

No charts, no nested menus, no settings pages.  The button calls `POST /v1/gatekeeper/protect`. The status indicator polls `GET /v1/gatekeeper/status` every 30 seconds.

**Backend-offline behaviour** depends on `VITE_DEMO_MODE`:
- `VITE_DEMO_MODE=false` (default): shows 🔴 STOPPED + "Backend offline" badge. No fake state changes.
- `VITE_DEMO_MODE=true`: shows "Demo mode" badge. Button press fakes local activation for UI demos.

**Supabase-not-configured behaviour**: shows "Supabase not configured" badge and 🔴 STOPPED. Button press returns `allowed=false` from the backend with reason `supabase_unavailable`.

**Accessibility built-in:**
- `aria-live="polite"` on the status region — screen readers announce state changes
- `aria-label` on every interactive element
- `aria-busy` on the action button during network calls
- Minimum 80 px button height — usable on touchscreens without precise tapping
- Skip-to-main-content link on first Tab press
- All colours pass WCAG AA contrast ratio on dark slate background

---

## Running Modes

### Mode A — Local Demo (no Supabase needed)

Use this to explore the UI and API shape without any database.

```bash
# Backend
cp .env.example .env
# Edit .env: set DEMO_MODE=true, GATEWAY_FAIL_OPEN=true
cd backend && uvicorn app.main:app --reload --port 8000

# Frontend
cp frontend/.env.example frontend/.env
# Edit frontend/.env: set VITE_DEMO_MODE=true
cd frontend && npm run dev
```

In demo mode:
- Backend starts without Supabase credentials
- All quota checks use the fail-open fallback (`allowed=true`)
- `/health` returns `ok`; `/status` shows `supabase_available: false, demo_mode: true`
- Frontend shows "Demo mode" badge; button press fakes activation when offline

### Mode B — Real Production (Supabase required)

The default. Both Supabase credentials and database migrations are required.

```bash
cp .env.example .env
# Edit .env: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (leave DEMO_MODE=false)
```

In production mode:
- Backend rejects all quota checks if Supabase is unreachable (`allowed=false, reason=supabase_unavailable`)
- `/health` returns `degraded` if Supabase client is not connected
- `/status` shows `supabase_available: true` only when connected
- Frontend shows "Supabase not configured" badge if `supabase_available=false`
- 🔴 STOPPED until the backend confirms quota is available

### Supabase Setup

Apply migrations in order, then seed:

```bash
npx supabase start   # local Postgres + PostgREST on port 54321

psql "$SUPABASE_DB_URL" -f database/migrations/001_billing_tiers.sql
psql "$SUPABASE_DB_URL" -f database/migrations/002_user_quotas.sql
psql "$SUPABASE_DB_URL" -f database/migrations/003_api_logs.sql
psql "$SUPABASE_DB_URL" -f database/migrations/004_indexes_and_rls.sql
psql "$SUPABASE_DB_URL" -f database/migrations/005_token_bucket_function.sql
psql "$SUPABASE_DB_URL" -f database/seeds/001_billing_tiers.sql
```

Provision a user (required before any protect call):

```bash
psql "$SUPABASE_DB_URL" -c "SELECT provision_user_quota('<user-uuid>'::uuid, 1);"
```

---

## Local Development

### Prerequisites

- Python 3.11+
- Node 20+
- [Supabase CLI](https://supabase.com/docs/guides/cli) (for Mode B)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/aivora-gatekeeper
cd aivora-gatekeeper
cp .env.example .env        # set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY for production
                            # or set DEMO_MODE=true for local demo
```

### 2. Start the backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API is live at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard is live at `http://localhost:5173`.

> For Supabase setup (Mode B), see [Supabase Setup](#supabase-setup) above.

---

## Environment Variables

All variables are read by the FastAPI backend from the `.env` file in the project root (or from the shell environment when deployed).  See `.env.example` for the authoritative list with descriptions.

### Required

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Full URL of your Supabase project (`https://<ref>.supabase.co` or local) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key — keep secret, never expose to the browser |

### LLM Providers

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | Routes to GPT-4o, Claude, Gemini, and others via a single endpoint |
| `GEMINI_API_KEY` | Google Gemini direct key (secondary fallback) |
| `LOCAL_LLM_FALLBACK_URL` | Base URL of a local OpenAI-compatible LLM (e.g. Ollama) used when all upstream providers fail |

### Runtime

| Variable | Default | Description |
|---|---|---|
| `ENV` | `development` | `development` · `staging` · `production` |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CORS_ORIGINS` | `*` | Comma-separated allowed browser origins |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Canonical frontend URL (used by reverse proxy / CDN config) |
| `GATEWAY_FAIL_OPEN` | `true` | Allow requests when Supabase is unreachable |
| `CB_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `CB_RECOVERY_TIMEOUT_SECONDS` | `60` | Seconds in OPEN state before HALF_OPEN probe |
| `TOKEN_DEFAULT_COST` | `10` | Default token cost when body is unreadable |
| `TOKEN_MAX_COST` | `10000` | Hard ceiling on any single request cost |

Full table with all tuning variables: see [`.env.example`](.env.example).

---

## Database Setup

Detailed schema documentation, migration commands, and token bucket design rationale are in [`database/README.md`](database/README.md).

Quick reference:

```bash
# Apply all migrations in order
for f in database/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done

# Provision a new user onto the Free tier
psql "$DATABASE_URL" -c "SELECT provision_user_quota('$USER_UUID'::uuid, 1);"
```

---

## Production Deployment

### Docker Compose

```bash
cp .env.example .env          # set ENV=production, GATEWAY_FAIL_OPEN=false
docker compose up --build -d
docker compose ps             # verify both services are healthy
```

> **Note:** The `docker-compose.yml` in this repo references `backend/Dockerfile` and `frontend/Dockerfile`. Add these before running the compose stack in production.

### Backend Dockerfile (example)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### Frontend Dockerfile (example)

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

### CORS in Production

Set `CORS_ORIGINS` to your exact frontend domain — never `*` in production:

```env
CORS_ORIGINS=https://app.yourcompany.com
FRONTEND_ORIGIN=https://app.yourcompany.com
```

### Supabase Connection Pooling

For high traffic (> 500 concurrent users), configure PgBouncer in transaction mode via Supabase's connection pooler and update `SUPABASE_URL` to point at the pooler port (6543).

### Scaling

The backend is stateless except for the in-memory quota cache and circuit breaker. Running multiple replicas behind a load balancer means each replica has its own in-memory state.  This is acceptable for the cache (slight over-spend during TTL window) but means each replica's circuit breaker trips independently.  For a fully centralised state, replace `InMemoryCache` with Redis and `CircuitBreaker` with a Redis-backed implementation — the module interfaces are designed to make this a drop-in swap.

---

## Project Structure

```
aivora-gatekeeper/
├── README.md                          ← this file
├── .env.example                       ← all environment variables with descriptions
├── .gitignore
├── docker-compose.yml                 ← local Docker stack
│
├── database/
│   ├── README.md                      ← token bucket design, schema reference
│   ├── migrations/
│   │   ├── 001_billing_tiers.sql      ← lookup table + set_updated_at trigger
│   │   ├── 002_user_quotas.sql        ← per-user bucket state + provision helper
│   │   ├── 003_api_logs.sql           ← immutable audit trail
│   │   ├── 004_indexes_and_rls.sql    ← 7 indexes + Row Level Security policies
│   │   └── 005_token_bucket_function.sql  ← process_token_bucket_leak()
│   └── seeds/
│       └── 001_billing_tiers.sql      ← Free / Pro / Enterprise tiers
│
├── backend/
│   ├── README.md                      ← run commands, API reference, architecture
│   ├── requirements.txt
│   └── app/
│       ├── main.py                    ← FastAPI app factory, lifespan, CORS
│       ├── core/
│       │   └── config.py              ← pydantic-settings, all env vars
│       ├── middleware/
│       │   └── gateway.py             ← GatewayMiddleware (quota enforcement)
│       ├── models/
│       │   └── schemas.py             ← Pydantic v2 request / response models
│       ├── routes/
│       │   └── gatekeeper.py          ← /health, /status, /protect, /simulate-request
│       ├── services/
│       │   ├── cache.py               ← InMemoryCache (TTL dict, async-safe)
│       │   ├── circuit_breaker.py     ← CLOSED/OPEN/HALF_OPEN state machine
│       │   └── supabase_client.py     ← async Supabase client + RPC wrapper
│       └── utils/
│           └── token_estimator.py     ← cost estimation for OpenAI / Anthropic bodies
│
└── frontend/
    ├── README.md                      ← run commands, accessibility notes
    ├── package.json                   ← React 19, Vite 8, Tailwind v4
    ├── index.html
    └── src/
        ├── main.tsx                   ← React root
        ├── App.tsx                    ← single-screen dashboard component
        ├── api.ts                     ← fetchGatekeeperStatus, callProtect, fallback data
        ├── types.ts                   ← TypeScript interfaces
        ├── index.css                  ← Tailwind import + minimal resets
        └── App.css                    ← (empty — all styles via Tailwind classes)
```

---

## API Quick Reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/v1/gatekeeper/status` | None | Circuit breaker state + cache stats |
| `POST` | `/v1/gatekeeper/protect` | None\* | Explicit quota check + token deduction |
| `POST` | `/v1/gatekeeper/simulate-request` | None\* | Read-only quota probe (no deduction) |

\* These routes bypass the `GatewayMiddleware` and perform their own quota logic. All other routes require `X-User-UUID: <uuid>` header and are subject to automatic quota enforcement.

Response headers on allowed requests:

```
X-RateLimit-Remaining: 9430
X-RateLimit-Cost: 250
X-Gatekeeper-Degraded: true    # only when circuit breaker is OPEN (degraded mode)
```

---

## License

Copyright © 2025 Aivora. All rights reserved.
