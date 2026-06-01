# Aivora Gatekeeper — Validation Checklist

Use this document to verify a fresh deployment end-to-end.  
Complete every item in order; each section depends on the previous one passing.

**Legend:** ☐ = not checked · ✓ = passed · ✗ = failed

---

## Pre-flight: Environment

| # | Check | Expected | Result |
|---|-------|----------|--------|
| P1 | `SUPABASE_URL` is set in `backend/.env` | Non-empty string | ☐ |
| P2 | `SUPABASE_SERVICE_ROLE_KEY` is set | Non-empty string | ☐ |
| P3 | `OPENROUTER_API_KEY` is set (proxy tests only) | Non-empty string | ☐ |
| P4 | `ADMIN_API_KEY` is set | Non-empty string | ☐ |
| P5 | Backend starts without errors | `INFO  startup app=Aivora Gatekeeper` in logs | ☐ |
| P6 | All 17 migrations applied (see MIGRATION_ORDER.md) | No errors in SQL editor | ☐ |
| P7 | Seed data applied (`python scripts/seed_demo.py`) | All 7 steps show ✓ | ☐ |

---

## Section 1: Health & Core Status

### 1.1 Liveness probe

```bash
curl http://localhost:8000/health
```

Expected:
```json
{ "status": "ok", "version": "1.0.0", "env": "development" }
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 1.1a | HTTP status | `200` | ☐ |
| 1.1b | `status` field | `"ok"` (not `"degraded"`) | ☐ |
| 1.1c | `version` field | Non-empty string | ☐ |

> If `status` is `"degraded"`, Supabase is unreachable. Check `SUPABASE_URL` and service role key.

---

### 1.2 Aggregator status (primary dashboard endpoint)

```bash
curl http://localhost:8000/v1/aggregator/status
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 1.2a | HTTP status | `200` | ☐ |
| 1.2b | `supabase_available` | `true` | ☐ |
| 1.2c | `status` | `"protected"` | ☐ |
| 1.2d | `circuit_breaker_state` | `"closed"` | ☐ |
| 1.2e | `stats.active_tiers` | `3` (from DB query) | ☐ |
| 1.2f | `openrouter_configured` | `true` if API key set | ☐ |
| 1.2g | `polar_configured` | `true` if Polar keys set | ☐ |

---

### 1.3 Gatekeeper status (circuit breaker diagnostics)

```bash
curl http://localhost:8000/v1/gatekeeper/status
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 1.3a | HTTP status | `200` | ☐ |
| 1.3b | `circuit_breaker.state` | `"closed"` | ☐ |
| 1.3c | `cache.total_entries` | `0` (fresh start) | ☐ |

---

## Section 2: Quota Enforcement Scenarios

All tests use `POST /v1/aggregator/check-usage`.  
Base URL: `http://localhost:8000`

### Scenario A: Monthly message limit exceeded (Bob, Free tier)

```bash
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000002",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 500, "estimated_cost": 0.001
  }'
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| A1 | HTTP status | `200` (gate response, not HTTP error) | ☐ |
| A2 | `allowed` | `false` | ☐ |
| A3 | `reason` | `"monthly_message_limit_exceeded"` | ☐ |
| A4 | `remaining_messages` | `0` | ☐ |

---

### Scenario B: Monthly dollar budget exceeded (Carol, Pro tier)

```bash
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000003",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 500, "estimated_cost": 0.10
  }'
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| B1 | HTTP status | `200` | ☐ |
| B2 | `allowed` | `false` | ☐ |
| B3 | `reason` | `"monthly_budget_exceeded"` | ☐ |
| B4 | `remaining_budget_usd` | `< 0.10` (less than $0.10 left) | ☐ |

---

### Scenario C: Workspace budget exceeded (Alice, Acme AI Co at $99.50/$100)

```bash
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000001",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 500, "estimated_cost": 1.00
  }'
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| C1 | HTTP status | `200` | ☐ |
| C2 | `allowed` | `false` | ☐ |
| C3 | `reason` | `"workspace_budget_exceeded"` | ☐ |
| C4 | `workspace_spend_usd` | `≈ 99.50` | ☐ |
| C5 | `workspace_remaining_usd` | `≈ 0.50` | ☐ |

Repeat with Dave (workspace admin, UUID `…0004`) — should get the same result:

| # | Check | Expected | Result |
|---|-------|----------|--------|
| C6 | Dave also blocked at workspace level | `allowed=false, reason=workspace_budget_exceeded` | ☐ |

---

### Scenario D: Account suspended (Eve)

```bash
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000005",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 100, "estimated_cost": 0.001
  }'
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| D1 | HTTP status | `200` | ☐ |
| D2 | `allowed` | `false` | ☐ |
| D3 | `reason` | `"workspace_suspended"` OR `"account_suspended"` | ☐ |

> Eve is both account-suspended AND in a suspended workspace. The workspace check fires first; the returned reason depends on lock ordering. Both are valid.

---

### Scenario E: Workspace suspension via Admin API

```bash
# Suspend the workspace via admin API
curl -X PATCH http://localhost:8000/v1/admin/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/suspend \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Test suspension"}'

# Immediately try a request from Alice (workspace member)
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000001",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 100, "estimated_cost": 0.001
  }'

# Unsuspend when done testing
curl -X PATCH http://localhost:8000/v1/admin/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/unsuspend \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| E1 | Suspend returns `{"suspended": true}` | Yes | ☐ |
| E2 | Immediate request blocked with `workspace_suspended` | `allowed=false` | ☐ |
| E3 | Unsuspend returns `{"suspended": false}` | Yes | ☐ |
| E4 | After unsuspend, Alice's small request succeeds | `allowed=true` | ☐ |

---

### Scenario F: Healthy request (Alice, tiny cost, workspace has headroom)

After unsuspending Acme AI Co, and with workspace at $99.50 (only $0.50 remaining):

```bash
curl -X POST http://localhost:8000/v1/aggregator/check-usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_uuid": "00000000-0000-0000-0000-000000000001",
    "provider": "openai", "model": "gpt-4o-mini",
    "estimated_tokens": 10, "estimated_cost": 0.0001
  }'
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| F1 | `allowed` | `true` | ☐ |
| F2 | `reason` | `"allowed"` | ☐ |
| F3 | `workspace_remaining_usd` | `≈ 0.4999` | ☐ |

---

## Section 3: Workspace & Analytics APIs

### 3.1 Workspace details

```bash
curl http://localhost:8000/v1/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.1a | `name` | `"Acme AI Co"` | ☐ |
| 3.1b | `plan` | `"growth"` | ☐ |
| 3.1c | `monthly_budget_usd` | `100.0` | ☐ |
| 3.1d | `is_suspended` | `false` | ☐ |

### 3.2 Workspace members

```bash
curl http://localhost:8000/v1/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/members
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.2a | Returns 3 members (Alice, Dave, Carol) | `length = 3` | ☐ |
| 3.2b | Alice's role | `"owner"` | ☐ |

### 3.3 Current-month usage

```bash
curl http://localhost:8000/v1/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/usage
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.3a | `total_cost_usd` | `≈ 99.50` (after scenario C) | ☐ |
| 3.3b | `total_requests` | `> 0` | ☐ |

### 3.4 Budget alerts

```bash
curl http://localhost:8000/v1/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/alerts
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.4a | `utilisation_pct` | `≥ 95.0` | ☐ |
| 3.4b | `alerts` array length | `≥ 3` (50%, 80%, 95% fired) | ☐ |
| 3.4c | Highest threshold | `95` | ☐ |

### 3.5 Spending anomalies

```bash
curl http://localhost:8000/v1/workspaces/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/anomalies
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.5a | Returns ≥ 1 anomaly | `length ≥ 1` | ☐ |
| 3.5b | At least one `spend_spike` anomaly | `anomaly_type = "spend_spike"` | ☐ |
| 3.5c | `resolved` on all returned rows | `false` | ☐ |

---

## Section 4: Invoice API

### 4.1 May 2026 invoice (historical month)

```bash
curl http://localhost:8000/v1/invoices/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/2026/5
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 4.1a | `period` | `"2026-05"` | ☐ |
| 4.1b | `total_cost_usd` | `22.36` | ☐ |
| 4.1c | `budget_utilisation_pct` | `22.36` (22.36% of $100) | ☐ |
| 4.1d | `budget_alerts` array | Includes 50% and 80% entries | ☐ |
| 4.1e | `member_breakdown` | Array of user usage objects | ☐ |

### 4.2 Usage trend

```bash
curl "http://localhost:8000/v1/invoices/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/trend?months=4"
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 4.2a | Returns 4 data points | `length = 4` | ☐ |
| 4.2b | Months in ascending order | Mar → Apr → May → Jun | ☐ |
| 4.2c | Each point has `total_cost_usd` and `total_requests` | Yes | ☐ |

---

## Section 5: Admin Dashboard API

All requests require `X-Admin-Key: $ADMIN_API_KEY`.

### 5.1 Platform stats

```bash
curl http://localhost:8000/v1/admin/stats \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 5.1a | `total_workspaces` | `2` | ☐ |
| 5.1b | `active_workspaces` | `1` (Frozen Corp suspended) | ☐ |
| 5.1c | `suspended_workspaces` | `1` | ☐ |
| 5.1d | `workspaces_over_80pct` | `≥ 1` | ☐ |
| 5.1e | `active_anomalies` | `≥ 1` | ☐ |
| 5.1f | Request without key → 403 | Yes | ☐ |

### 5.2 Workspace list

```bash
curl http://localhost:8000/v1/admin/workspaces \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 5.2a | Returns both workspaces | `length = 2` | ☐ |
| 5.2b | Acme AI Co `budget_utilisation_pct` | `≥ 95.0` | ☐ |
| 5.2c | Acme AI Co `active_anomalies` | `≥ 1` | ☐ |
| 5.2d | Frozen Corp `is_suspended` | `true` | ☐ |

### 5.3 Anomaly feed

```bash
curl http://localhost:8000/v1/admin/anomalies \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 5.3a | Returns ≥ 2 anomalies | Yes | ☐ |
| 5.3b | `spend_spike` anomaly present | Yes | ☐ |
| 5.3c | `budget_trajectory` anomaly present | Yes | ☐ |
| 5.3d | Resolve one anomaly and verify it disappears | `PATCH /anomalies/{id}/resolve` | ☐ |

---

## Section 6: Frontend UI

Open `http://localhost:5173` in a browser.

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 6.1 | Page loads without console errors | Yes | ☐ |
| 6.2 | Status indicator shows 🟢 PROTECTED | Backend + Supabase connected | ☐ |
| 6.3 | "Activate Billing Shield" button visible | Yes | ☐ |
| 6.4 | Click button → `allowed: true` response | "✅ Billing shield activated" message | ☐ |
| 6.5 | Metric cards show non-zero values after activation | Yes | ☐ |
| 6.6 | Circuit breaker state in footer | `CLOSED` | ☐ |
| 6.7 | AI Request Simulation — click ▶ Allowed | 5 green steps animate in sequence | ☐ |
| 6.8 | AI Request Simulation — click ▶ Blocked | 4 steps ending with red ❌ Blocked | ☐ |

---

## Section 7: API Documentation

```
http://localhost:8000/docs
```

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 7.1 | `/docs` opens Swagger UI | Yes | ☐ |
| 7.2 | New tags visible: Workspaces, Admin, Invoices | Yes | ☐ |
| 7.3 | `POST /v1/aggregator/proxy-openrouter` visible | Yes | ☐ |
| 7.4 | `GET /v1/admin/stats` shows X-Admin-Key parameter | Yes | ☐ |

---

## Screenshot Checklist

Capture the following screenshots in order. Save them with the suggested filename for your demo or PR.

| # | URL / Command | What to show | Filename |
|---|---------------|-------------|----------|
| S1 | `GET /health` | `{"status": "ok"}` response | `01-health-ok.png` |
| S2 | `GET /v1/aggregator/status` | Full JSON with `status: protected, supabase_available: true` | `02-aggregator-status.png` |
| S3 | Frontend `http://localhost:5173` | 🟢 PROTECTED status, button, 4 metric cards | `03-frontend-protected.png` |
| S4 | Scenario A curl | `allowed: false, reason: monthly_message_limit_exceeded` | `04-scenario-a-quota.png` |
| S5 | Scenario B curl | `allowed: false, reason: monthly_budget_exceeded` | `05-scenario-b-budget.png` |
| S6 | Scenario C curl | `allowed: false, reason: workspace_budget_exceeded, workspace_spend_usd: 99.5` | `06-scenario-c-workspace.png` |
| S7 | Scenario D/E curl | `allowed: false, reason: account_suspended or workspace_suspended` | `07-scenario-d-suspended.png` |
| S8 | `GET /v1/workspaces/{id}/alerts` | `utilisation_pct ≥ 95, alerts array with 3 thresholds` | `08-budget-alerts.png` |
| S9 | `GET /v1/workspaces/{id}/anomalies` | At least spend_spike anomaly with deviation_pct | `09-anomalies.png` |
| S10 | `GET /v1/invoices/{id}/2026/5` | Full invoice JSON with member_breakdown | `10-invoice-may.png` |
| S11 | `GET /v1/admin/stats` with key | Platform-wide stats with active_anomalies | `11-admin-stats.png` |
| S12 | `GET /v1/admin/workspaces` | Both workspaces enriched with budget_utilisation_pct | `12-admin-workspaces.png` |
| S13 | Suspend + immediate block + unsuspend | 3 responses demonstrating live suspension | `13-suspend-cycle.png` |
| S14 | `http://localhost:8000/docs` | Swagger UI showing all 5 route groups | `14-swagger-docs.png` |
| S15 | `python scripts/validate.py` output | All PASS (or document known failures) | `15-validate-output.png` |

---

## Automated validation

Run after completing sections 1–5:

```bash
cd backend
python ../scripts/validate.py --base-url http://localhost:8000 --admin-key $ADMIN_API_KEY
```

All checks should output `PASS`. Exit code `0` = all passed; non-zero = failures.

---

## Known limitations to document

- `workspace_monthly_usage.unique_users` is always `0` (not computed by RPC — add to known gaps)
- Anomaly detection fires only when `workspace_check_and_consume_usage` is called; freshly seeded anomalies inserted directly via SQL will not trigger re-detection until new usage arrives
- Budget alert threshold events are inserted by the SQL RPC; the seed inserts them directly to simulate past state
- `GET /health` returns `"degraded"` when circuit breaker is OPEN — expected during Supabase outage testing
