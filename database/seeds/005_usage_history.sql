-- Seed: 005_usage_history
-- Three months of historical workspace_monthly_usage + per-user usage_counters.
-- Current month (June 2026) is seeded with partial data (day 1 of month).
-- Historical months: March, April, May 2026.
--
-- Designed so the anomaly detector fires on the June data:
--   • Daily rate in June: $4.12/day
--   • Historical average:  $0.75/day   (over Mar–May)
--   • Spike ratio:         5.5×  → severity HIGH (> 3× threshold)
--   • Projected June total: $4.12 × 30 = $123.60 > $100 budget → budget_trajectory
--
-- Also seeds usage_counters for scenario states (see 006_scenario_states.sql).

-- ── workspace_monthly_usage: Acme AI Co ──────────────────────────────────────

INSERT INTO public.workspace_monthly_usage
    (workspace_id, year, month,
     total_requests, blocked_requests, total_tokens, total_cost_usd,
     last_request_at, updated_at)
VALUES
    -- March 2026: normal month
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 3,
     213, 4, 1_065_000, 19.87,
     '2026-03-31 23:47:12+00', '2026-03-31 23:47:12+00'),

    -- April 2026: slightly higher usage
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 4,
     287, 6, 1_435_000, 24.53,
     '2026-04-30 22:18:44+00', '2026-04-30 22:18:44+00'),

    -- May 2026: highest month yet, triggered 80% alert
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 5,
     341, 11, 1_705_000, 22.36,
     '2026-05-31 21:55:03+00', '2026-05-31 21:55:03+00'),

    -- June 2026: current month — abnormal spike on day 1
    -- $4.12 in day 1 vs $0.75 historical average → triggers spend_spike + budget_trajectory
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 6,
     47, 2, 235_000, 4.12,
     now() - interval '5 minutes', now())

ON CONFLICT (workspace_id, year, month) DO UPDATE
    SET total_requests   = EXCLUDED.total_requests,
        blocked_requests = EXCLUDED.blocked_requests,
        total_tokens     = EXCLUDED.total_tokens,
        total_cost_usd   = EXCLUDED.total_cost_usd,
        last_request_at  = EXCLUDED.last_request_at,
        updated_at       = now();

-- ── workspace_monthly_usage: Frozen Corp (suspended workspace) ────────────────
-- Has some usage before it was suspended (suspicious pattern: high spend in Jan/Feb)

INSERT INTO public.workspace_monthly_usage
    (workspace_id, year, month,
     total_requests, blocked_requests, total_tokens, total_cost_usd,
     last_request_at, updated_at)
VALUES
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 4,
     1204, 89, 6_020_000, 48.92,
     '2026-04-30 11:22:01+00', '2026-04-30 11:22:01+00'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 5,
     2847, 312, 14_235_000, 117.43,   -- massively over $50 budget → fraud signal
     '2026-05-28 03:14:59+00', '2026-05-28 03:14:59+00')
ON CONFLICT (workspace_id, year, month) DO UPDATE
    SET total_requests   = EXCLUDED.total_requests,
        blocked_requests = EXCLUDED.blocked_requests,
        total_tokens     = EXCLUDED.total_tokens,
        total_cost_usd   = EXCLUDED.total_cost_usd,
        last_request_at  = EXCLUDED.last_request_at,
        updated_at       = now();

-- ── usage_counters: per-user monthly rows ─────────────────────────────────────
-- period_start is always first-of-month UTC (matches check_and_consume logic)

INSERT INTO public.usage_counters
    (user_uuid, period_start, messages_used, budget_used_usd, last_updated_at)
VALUES
    -- Alice: current month (healthy — 47 messages of 1000, $2.18 of $20)
    ('00000000-0000-0000-0000-000000000001', date_trunc('month', now()),  47, 2.18, now()),

    -- Alice: previous months
    ('00000000-0000-0000-0000-000000000001', '2026-03-01 00:00:00+00', 118, 6.32, '2026-03-31 23:00:00+00'),
    ('00000000-0000-0000-0000-000000000001', '2026-04-01 00:00:00+00', 156, 8.91, '2026-04-30 22:00:00+00'),
    ('00000000-0000-0000-0000-000000000001', '2026-05-01 00:00:00+00', 134, 7.44, '2026-05-31 21:00:00+00'),

    -- Bob (Free): current month — will be set to 50/50 in scenario seed
    ('00000000-0000-0000-0000-000000000002', date_trunc('month', now()),  38, 0.31, now()),

    -- Carol (Pro): current month — will be set to near-budget in scenario seed
    ('00000000-0000-0000-0000-000000000003', date_trunc('month', now()), 821, 17.43, now()),
    ('00000000-0000-0000-0000-000000000003', '2026-05-01 00:00:00+00', 207, 11.22, '2026-05-31 21:00:00+00'),

    -- Dave (Enterprise): healthy
    ('00000000-0000-0000-0000-000000000004', date_trunc('month', now()), 412, 31.87, now()),
    ('00000000-0000-0000-0000-000000000004', '2026-05-01 00:00:00+00', 531, 44.23, '2026-05-31 21:00:00+00')

ON CONFLICT (user_uuid, period_start) DO UPDATE
    SET messages_used    = EXCLUDED.messages_used,
        budget_used_usd  = EXCLUDED.budget_used_usd,
        last_updated_at  = EXCLUDED.last_updated_at;
