-- Seed: 006_scenario_states
-- Forces four explicit quota-enforcement scenarios into the database.
-- Each scenario can be tested immediately after this seed runs.
--
-- Scenarios:
--   A. Bob  → monthly_message_limit_exceeded  (50/50 Free messages used)
--   B. Carol → monthly_budget_exceeded         (budget_used_usd >= tier budget)
--   C. Alice via workspace → workspace_budget_exceeded  (workspace at $99.50)
--   D. Eve  → account_suspended               (user_quotas.is_suspended = true)
--   E. Frozen Corp → workspace_suspended      (workspaces.is_suspended = true)
--
-- To TEST each scenario after seeding, call:
--   POST /v1/aggregator/check-usage  { "user_uuid": "<uuid>", ... }
-- and verify the returned reason matches the expected code.

-- ── Scenario A: Bob — monthly message limit exhausted ─────────────────────────
-- Free tier allows 50 messages/month. Set messages_used = 50.

UPDATE public.usage_counters
   SET messages_used   = 50,
       budget_used_usd = 0.47,
       last_updated_at = now()
 WHERE user_uuid    = '00000000-0000-0000-0000-000000000002'
   AND period_start = date_trunc('month', now());

-- ── Scenario B: Carol — monthly dollar budget exhausted ───────────────────────
-- Pro tier monthly_budget_usd = $20.00.
-- Set budget_used_usd = $19.97 (any new request estimated >= $0.03 will block).

UPDATE public.usage_counters
   SET messages_used   = 892,
       budget_used_usd = 19.97,
       last_updated_at = now()
 WHERE user_uuid    = '00000000-0000-0000-0000-000000000003'
   AND period_start = date_trunc('month', now());

-- ── Scenario C: Acme AI Co workspace — workspace budget near-exhausted ────────
-- Set workspace total_cost_usd to $99.50 of $100.00 budget.
-- Any request estimated >= $0.50 will trigger workspace_budget_exceeded.
-- Note: Alice and Dave are workspace members — their requests will be blocked
-- at the workspace gate even though their personal quotas are fine.

UPDATE public.workspace_monthly_usage
   SET total_cost_usd   = 99.50,
       total_requests   = 952,
       blocked_requests = 14,
       last_request_at  = now() - interval '3 minutes',
       updated_at       = now()
 WHERE workspace_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
   AND year         = EXTRACT(YEAR  FROM now())::smallint
   AND month        = EXTRACT(MONTH FROM now())::smallint;

-- ── Scenario D: Eve — account suspended ──────────────────────────────────────
-- Already set in 004_demo_user_quotas.sql but re-confirmed here for clarity.

UPDATE public.user_quotas
   SET is_suspended      = true,
       suspension_reason = 'Policy violation: automated abuse detection triggered'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000005';

-- ── Scenario E: Frozen Corp — workspace suspended ────────────────────────────
-- Already set in 003_demo_workspace.sql but re-confirmed here for clarity.

UPDATE public.workspaces
   SET is_suspended      = true,
       suspension_reason = 'Fraudulent activity detected — account under review. Ref: FRAUD-2026-0601',
       updated_at        = now()
 WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';

-- ── Scenario reference table (for documentation / test runners) ───────────────
-- This is a comment block only — no actual table is created.
--
-- | Scenario | user_uuid suffix | Expected reason                 | HTTP |
-- |----------|------------------|---------------------------------|------|
-- | A        | ...0002 (Bob)    | monthly_message_limit_exceeded  |  429 |
-- | B        | ...0003 (Carol)  | monthly_budget_exceeded         |  429 |
-- | C        | ...0001 (Alice)  | workspace_budget_exceeded       |  429 |
-- | C        | ...0004 (Dave)   | workspace_budget_exceeded       |  429 |
-- | D        | ...0005 (Eve)    | account_suspended               |  403 |
-- | E        | ...0005 (Eve)    | workspace_suspended             |  403 |
--
-- Note: Scenarios C + D/E overlap for Eve (both workspace suspended AND
-- account suspended). The workspace check fires first, so the reason
-- returned is workspace_suspended for Eve.
