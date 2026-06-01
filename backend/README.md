# Backend — Aivora Gatekeeper

FastAPI token-bucket rate-limiting engine for AI API traffic.

## Quick Start

```bash
cd backend

# 1. Create and activate the virtual environment
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment template and fill in your Supabase credentials
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux

# 4. Run the development server (auto-reload)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Environment Variables

Copy `.env.example` → `.env` and set the values:

| Variable | Default | Description |
|---|---|---|
| `SUPABASE_URL` | `http://127.0.0.1:54321` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | _(required)_ | Service role key with RPC access |
| `ENV` | `development` | `development` \| `staging` \| `production` |
| `DEBUG` | `false` | Enable FastAPI debug mode |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins, or `*` |
| `CACHE_DEFAULT_TTL_SECONDS` | `5` | Positive-decision cache TTL |
| `CACHE_NEGATIVE_TTL_SECONDS` | `15` | Blocked-user cache TTL |
| `CACHE_MAX_ENTRIES` | `10000` | Max in-memory cache entries |
| `CB_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `CB_RECOVERY_TIMEOUT_SECONDS` | `60` | Seconds before HALF_OPEN probe |
| `CB_HALF_OPEN_MAX_ATTEMPTS` | `2` | Successes needed to close circuit |
| `TOKEN_CHARS_PER_TOKEN` | `4.0` | Chars-per-token estimation ratio |
| `TOKEN_DEFAULT_COST` | `10` | Default cost when body is unreadable |
| `TOKEN_MAX_COST` | `10000` | Hard ceiling on any single request cost |
| `GATEWAY_BYPASS_PATHS` | _(see config)_ | Comma-separated paths that skip quota |
| `GATEWAY_FAIL_OPEN` | `true` | Allow requests when Supabase is unreachable |

---

## API Endpoints

### `GET /health`
Liveness probe. Returns `200 ok` or `200 degraded` when the circuit breaker is open.

```bash
curl http://localhost:8000/health
```

### `GET /v1/gatekeeper/status`
Diagnostics: circuit breaker state, cache hit/miss counters.

```bash
curl http://localhost:8000/v1/gatekeeper/status
```

### `POST /v1/gatekeeper/protect`
Gate an upstream LLM request. **Deducts tokens on allow.**

```bash
curl -X POST http://localhost:8000/v1/gatekeeper/protect \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000001",
    "endpoint": "/v1/chat/completions",
    "http_method": "POST",
    "body": {
      "messages": [{"role": "user", "content": "Hello, world!"}],
      "max_tokens": 256
    }
  }'
```

Response:
```json
{
  "decision": {
    "allowed": true,
    "remaining_tokens": 9683.0,
    "reason": "allowed",
    "estimated_cost": 317,
    "degraded_mode": false
  },
  "user_uuid": "00000000-0000-0000-0000-000000000001",
  "cache_hit": false
}
```

### `POST /v1/gatekeeper/simulate-request`
Read-only quota probe. **Does NOT deduct tokens.** Uses cached state only.

```bash
curl -X POST http://localhost:8000/v1/gatekeeper/simulate-request \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000001",
    "cost_override": 500
  }'
```

---

## Middleware: Automatic Quota Enforcement

All routes **not** in `GATEWAY_BYPASS_PATHS` are automatically protected.
Include `X-User-UUID` on every request:

```bash
curl -X POST http://localhost:8000/v1/your-ai-endpoint \
  -H "X-User-UUID: 00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{ "messages": [...] }'
```

On quota exhaustion the middleware returns `429` with:
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

Response headers on allowed requests:
```
X-RateLimit-Remaining: 9430
X-RateLimit-Cost: 250
X-Gatekeeper-Degraded: true   # only present during circuit-open degraded mode
```

---

## Architecture

```
Request
  │
  ▼
CORSMiddleware        (FastAPI built-in)
  │
  ▼
GatewayMiddleware     (app/middleware/gateway.py)
  ├── Validate X-User-UUID
  ├── Read + estimate body cost
  ├── Check in-memory cache (negative fast-reject)
  ├── process_token_bucket()
  │     └── CircuitBreaker.call()
  │           ├── CLOSED / HALF_OPEN → Supabase RPC
  │           └── OPEN               → fallback (fail-open)
  └── Attach X-RateLimit-* headers
  │
  ▼
Route handler         (app/routes/gatekeeper.py)
```

### Circuit Breaker States

```
CLOSED ──(5 failures)──► OPEN ──(60 s)──► HALF_OPEN
  ▲                                            │
  └──────────────(2 successes)─────────────────┘
```

---

## Running Tests

```bash
pytest tests/ -v
```

## Type Checking

```bash
mypy app/
```

## Linting

```bash
ruff check app/
ruff format app/
```
