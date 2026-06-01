# Database Layer — Aivora Gatekeeper

## Overview

The database layer provides billing-tier configuration, per-user token bucket state, and an immutable audit trail for every API request decision.

---

## Token Bucket Design

### Algorithm

This implementation uses the **continuous-refill token bucket** pattern (sometimes called "leaky bucket as a meter").

```
bucket level
    ↑ max_tokens ──────────────────────────────────────────────────
    │                          ╭─╮          ╭─────────
    │               ╭──────────╯ ╰──────────╯
    │    ╭──────────╯
    │────╯
    └──────────────────────────────────────────────────────────→ time
           refill at refill_rate tokens/sec, capped at max_tokens
```

Each incoming request carries a `request_cost`. The gate function:

1. **Locks** the user's `user_quotas` row with `SELECT … FOR UPDATE` — this serialises all concurrent calls for the same user at the database level, eliminating race conditions without application-side locking or Redis.
2. **Refills** the bucket: `new_tokens = MIN(current_tokens + elapsed_secs × refill_rate, max_tokens)`.
3. **Checks the monthly budget** hard ceiling independently of the bucket.
4. **Decides**: if `new_tokens >= request_cost`, deduct and allow; otherwise leave the bucket unchanged and reject.
5. **Writes** an immutable row to `api_logs` regardless of outcome.
6. **Returns** `{ allowed, remaining_tokens, reason }` as JSONB.

### Why PostgreSQL row locking instead of Redis?

| Concern | PostgreSQL FOR UPDATE | Redis INCR / Lua |
|---|---|---|
| Correctness under concurrent load | Serialised at row level | Requires careful Lua scripting |
| Audit trail atomicity | Same transaction as log write | Separate round-trip |
| Operational complexity | Already present | Extra infra dependency |
| Throughput | ~10–50 k RPS per Supabase instance | Millions of RPS |

For a gatekeeper service handling AI API traffic (latency already 100 ms+), PostgreSQL is sufficient and keeps the stack simpler. Migrate to Redis if you need sub-millisecond decisions at > 50 k concurrent users.

### Reason codes returned by `process_token_bucket_leak`

| Reason | Meaning |
|---|---|
| `allowed` | Request passed; tokens deducted |
| `insufficient_tokens` | Bucket below `request_cost`; try again after refill time |
| `monthly_budget_exceeded` | Hard monthly cap reached; resets next calendar month |
| `suspended` | Account administratively suspended |
| `quota_not_found` | No `user_quotas` row exists; call `provision_user_quota` first |
| `internal_error` | Unexpected DB error; safe fail-closed |

---

## Schema

### `billing_tiers`

Lookup table. One row per subscription plan. Seeded at deploy time.

| Column | Type | Purpose |
|---|---|---|
| `id` | `smallint` | PK (1 = Free, 2 = Pro, 3 = Enterprise) |
| `name` | `text` | Unique tier name displayed in UI |
| `max_tokens` | `integer` | Bucket capacity (burst ceiling) |
| `refill_rate` | `numeric` | Tokens added per second |
| `monthly_token_budget` | `bigint` | Hard monthly ceiling |
| `price_usd_cents` | `integer` | Billing amount |

### `user_quotas`

One row per user. Hot path: read + write on every request.

| Column | Type | Purpose |
|---|---|---|
| `user_uuid` | `uuid` | FK → `auth.users.id` |
| `current_tokens` | `numeric` | Live bucket level |
| `last_refill_at` | `timestamptz` | Timestamp used to calculate continuous refill |
| `max_tokens` | `integer` | Copied from tier at provision time (allows per-user overrides) |
| `refill_rate` | `numeric` | Copied from tier |
| `monthly_token_budget` | `bigint` | Copied from tier |
| `period_start` | `timestamptz` | Start of current billing window |
| `tokens_used_this_period` | `bigint` | Monotonically increasing until period rolls |
| `is_suspended` | `boolean` | Administrative kill switch |

### `api_logs`

Append-only audit trail. Never `UPDATE` or `DELETE` rows.

| Column | Type | Purpose |
|---|---|---|
| `id` | `bigint` | Sequential PK |
| `user_uuid` | `uuid` | FK → `auth.users.id` |
| `endpoint` | `text` | Caller-supplied route label |
| `request_id` | `text` | Distributed trace correlation ID |
| `request_cost` | `integer` | Tokens requested |
| `tokens_before` | `numeric` | Bucket level after refill, before deduction |
| `tokens_after` | `numeric` | Bucket level after decision |
| `allowed` | `boolean` | Gate decision |
| `reason` | `text` | Machine-readable outcome code |
| `processing_ms` | `integer` | Latency of the gate function itself |

---

## Applying Migrations

Migrations are intended to be run in order via the Supabase CLI or directly in the SQL editor.

```bash
# via Supabase CLI (recommended)
supabase db push

# or manually, in order:
psql "$DATABASE_URL" -f database/migrations/001_billing_tiers.sql
psql "$DATABASE_URL" -f database/migrations/002_user_quotas.sql
psql "$DATABASE_URL" -f database/migrations/003_api_logs.sql
psql "$DATABASE_URL" -f database/migrations/004_indexes_and_rls.sql
psql "$DATABASE_URL" -f database/migrations/005_token_bucket_function.sql

# seed reference data
psql "$DATABASE_URL" -f database/seeds/001_billing_tiers.sql
```

## Provisioning a New User

After a user signs up via Supabase Auth, call:

```sql
SELECT provision_user_quota('<user-uuid>'::uuid, 1);  -- 1 = Free tier
```

Or from the FastAPI backend:

```python
await supabase.rpc("provision_user_quota", {
    "p_user_uuid": str(user.id),
    "p_billing_tier_id": 1
}).execute()
```

## Calling the Gate

```python
result = await supabase.rpc("process_token_bucket_leak", {
    "p_user_uuid":    str(user.id),
    "p_request_cost": 10,
    "p_endpoint":     "/v1/chat",
    "p_http_method":  "POST",
    "p_request_id":   request.headers.get("x-request-id"),
}).execute()

data = result.data  # { "allowed": true, "remaining_tokens": 9843.2, "reason": "allowed" }
if not data["allowed"]:
    raise HTTPException(status_code=429, detail=data["reason"])
```
