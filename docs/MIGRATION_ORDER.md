# Migration Order

Apply every migration **in the exact order listed below** before running any seed script or starting the backend in production mode.

All files live in `database/migrations/`.

---

## Apply via psql (local Supabase CLI)

```bash
# Start local Supabase (first time only)
npx supabase start

# Export the direct DB connection URL
export DB_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# Apply all migrations in order
psql "$DB_URL" -f database/migrations/001_billing_tiers.sql
psql "$DB_URL" -f database/migrations/002_user_quotas.sql
psql "$DB_URL" -f database/migrations/003_api_logs.sql
psql "$DB_URL" -f database/migrations/004_indexes_and_rls.sql
psql "$DB_URL" -f database/migrations/005_token_bucket_function.sql
psql "$DB_URL" -f database/migrations/006_subscriptions.sql
psql "$DB_URL" -f database/migrations/007_usage_counters.sql
psql "$DB_URL" -f database/migrations/008_provider_costs.sql
psql "$DB_URL" -f database/migrations/009_monthly_usage_resets.sql
psql "$DB_URL" -f database/migrations/010_polar_webhook_events.sql
psql "$DB_URL" -f database/migrations/011_aggregator_rpc.sql
psql "$DB_URL" -f database/migrations/012_workspaces.sql
psql "$DB_URL" -f database/migrations/013_workspace_usage.sql
psql "$DB_URL" -f database/migrations/014_budget_alerts.sql
psql "$DB_URL" -f database/migrations/015_spending_anomalies.sql
psql "$DB_URL" -f database/migrations/016_workspace_quota_rpc.sql
psql "$DB_URL" -f database/migrations/017_workspace_analytics_rpc.sql
```

Or as a one-liner:

```bash
for f in database/migrations/*.sql; do echo "Applying $f…"; psql "$DB_URL" -f "$f"; done
```

---

## Apply via Supabase SQL Editor (hosted project)

Open **Supabase Dashboard → SQL Editor → New query** and paste each file's content, running them one at a time in the order below.

---

## Migration reference

| # | File | What it creates | Depends on |
|---|------|----------------|------------|
| 001 | `001_billing_tiers.sql` | `billing_tiers` table + `set_updated_at()` trigger function | — |
| 002 | `002_user_quotas.sql` | `user_quotas` table + `provision_user_quota()` RPC | 001 |
| 003 | `003_api_logs.sql` | `api_logs` immutable audit table | 002 |
| 004 | `004_indexes_and_rls.sql` | Indexes + Row Level Security policies | 001–003 |
| 005 | `005_token_bucket_function.sql` | `process_token_bucket_leak()` RPC | 001–004 |
| 006 | `006_subscriptions.sql` | `subscriptions` table + billing tier message/budget limits | 001–002 |
| 007 | `007_usage_counters.sql` | `usage_counters` table (monthly message + dollar counters) | 002 |
| 008 | `008_provider_costs.sql` | `provider_costs` table (USD pricing for 40+ models) | — |
| 009 | `009_monthly_usage_resets.sql` | Scheduled reset function for period rollover | 002 |
| 010 | `010_polar_webhook_events.sql` | `polar_webhook_events` table (idempotent webhook log) | 002 |
| 011 | `011_aggregator_rpc.sql` | `check_and_consume_ai_usage()` RPC (user quota gate) | 001–007 |
| 012 | `012_workspaces.sql` | `workspaces` + `workspace_members` tables; `user_quotas.workspace_id` FK; `create_workspace()` RPC | 002 |
| 013 | `013_workspace_usage.sql` | `workspace_monthly_usage` aggregate table | 012 |
| 014 | `014_budget_alerts.sql` | `workspace_budget_alerts` threshold events table | 012 |
| 015 | `015_spending_anomalies.sql` | `spending_anomalies` table | 012 |
| 016 | `016_workspace_quota_rpc.sql` | `workspace_check_and_consume_usage()` hierarchical gate + `_fire_budget_alerts()` helper | 011–015 |
| 017 | `017_workspace_analytics_rpc.sql` | `get_workspace_invoice_summary()` + `get_workspace_usage_trend()` + `get_platform_stats()` RPCs | 012–015 |

---

## Seeds (apply after all migrations)

| # | File | What it inserts |
|---|------|-----------------|
| 001 | `database/seeds/001_billing_tiers.sql` | Free / Pro / Enterprise tier rows (idempotent) |
| 002 | `database/seeds/002_demo_auth_users.sql` | 5 demo users in `auth.users` (requires service_role) |
| 003 | `database/seeds/003_demo_workspace.sql` | Acme AI Co + Frozen Corp workspaces + members |
| 004 | `database/seeds/004_demo_user_quotas.sql` | Provisions `user_quotas` for demo users via RPC |
| 005 | `database/seeds/005_usage_history.sql` | 3 months historical usage + current month partial |
| 006 | `database/seeds/006_scenario_states.sql` | Forces quota/budget/suspension scenario states |
| 007 | `database/seeds/007_alerts_and_anomalies.sql` | Budget alert events + spending anomaly rows |

> **Preferred method**: run `python scripts/seed_demo.py` which handles auth user creation via the Supabase Admin API and executes all seed operations in the correct order.

---

## Minimum migrations for production (no workspace features)

If you are deploying without the workspace/admin/invoice features, you only need:

```
001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011
```

The workspace layer (012–017) can be applied later without breaking the existing quota system.

---

## Rollback notes

- Migrations 001–011 have no rollback scripts. To roll back, restore from a DB snapshot or drop the affected tables manually.
- Migrations 012–017 add new tables and columns only. Rolling back requires:
  1. `DROP TABLE workspace_monthly_usage, workspace_budget_alerts, spending_anomalies, workspace_members, workspaces CASCADE;`
  2. `ALTER TABLE user_quotas DROP COLUMN IF EXISTS workspace_id;`
  3. `DROP FUNCTION IF EXISTS workspace_check_and_consume_usage, _fire_budget_alerts, get_workspace_invoice_summary, get_workspace_usage_trend, get_platform_stats, create_workspace;`
