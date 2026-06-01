-- Seed: 007_alerts_and_anomalies
-- Seeds budget alert events and spending anomalies for the admin dashboard.
--
-- Budget alerts (workspace_budget_alerts):
--   Acme AI Co — 50% alert in April 2026
--   Acme AI Co — 50% + 80% alerts in May 2026
--   Acme AI Co — 50% + 80% + 95% alerts in June 2026 (current — scenario C)
--   Frozen Corp — 50% + 80% + 95% + 100% alerts in May 2026 (massively over budget)
--
-- Spending anomalies (spending_anomalies):
--   Acme AI Co — spend_spike        (today, severity: high)
--   Acme AI Co — budget_trajectory  (today, severity: high)
--   Frozen Corp — spend_spike       (May, severity: critical, resolved)

-- ── Budget alerts ─────────────────────────────────────────────────────────────

INSERT INTO public.workspace_budget_alerts
    (workspace_id, year, month, threshold_pct, spend_at_trigger, budget_usd, triggered_at)
VALUES
    -- Acme AI Co — April (50% = $50 of $100)
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 4, 50, 51.23, 100.00, '2026-04-19 14:32:07+00'),

    -- Acme AI Co — May (50% and 80%)
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 5, 50, 50.84, 100.00, '2026-05-17 09:11:22+00'),
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 5, 80, 80.12, 100.00, '2026-05-27 16:44:51+00'),

    -- Acme AI Co — June (all three, reflecting scenario C state of $99.50)
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 6, 50, 50.31, 100.00, now() - interval '6 hours'),
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 6, 80, 80.77, 100.00, now() - interval '4 hours'),
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2026, 6, 95, 95.14, 100.00, now() - interval '2 hours'),

    -- Frozen Corp — May (all four — massively over $50 budget)
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 5, 50,  25.43, 50.00, '2026-05-07 02:14:33+00'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 5, 80,  40.22, 50.00, '2026-05-12 06:55:19+00'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 5, 95,  47.91, 50.00, '2026-05-15 11:03:47+00'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 2026, 5, 100, 50.01, 50.00, '2026-05-15 11:47:22+00')

ON CONFLICT (workspace_id, year, month, threshold_pct) DO NOTHING;

-- ── Spending anomalies ────────────────────────────────────────────────────────

INSERT INTO public.spending_anomalies
    (workspace_id, anomaly_type, severity,
     current_value, baseline_value, deviation_pct,
     description, resolved, detected_at)
VALUES
    -- Acme AI Co: spend_spike — today (unresolved)
    -- Daily rate $4.12 vs historical avg $0.75/day = 5.5× baseline
    (
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        'spend_spike',
        'high',
        4.120000,   -- current daily rate USD
        0.750000,   -- 3-month rolling average daily rate
        449.33,     -- ((4.12/0.75) - 1) * 100
        'Daily spend rate $4.12/day is 449% above the 3-month average ($0.75/day). '
        'Possible causes: new high-cost model deployed, runaway automation, or credential leak.',
        false,
        now() - interval '45 minutes'
    ),

    -- Acme AI Co: budget_trajectory — today (unresolved)
    -- Projected: $4.12/day × 30 = $123.60 vs $100 budget = 123.6%
    (
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        'budget_trajectory',
        'high',
        123.600000,  -- projected month-end spend
        100.000000,  -- workspace monthly budget
        23.60,       -- over-budget %
        'At current rate ($4.12/day), projected month-end spend is $123.60 — '
        '123.6% of the $100.00 budget (1 day into the month). '
        'Budget will be exhausted around day 24 if rate continues.',
        false,
        now() - interval '40 minutes'
    ),

    -- Frozen Corp: spend_spike — May 2026 (resolved after suspension)
    (
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        'spend_spike',
        'critical',
        91.340000,  -- daily rate during fraud period
        1.630000,   -- previous baseline
        5503.68,
        'Critical: daily spend rate $91.34/day is 5504% above the baseline ($1.63/day). '
        'Fraud pattern detected. Workspace suspended pending review.',
        true,
        '2026-05-14 08:22:11+00'
    )

ON CONFLICT DO NOTHING;

-- Mark the Frozen Corp anomaly as resolved
UPDATE public.spending_anomalies
   SET resolved    = true,
       resolved_at = '2026-05-28 09:00:00+00'
 WHERE workspace_id  = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
   AND anomaly_type  = 'spend_spike'
   AND detected_at   = '2026-05-14 08:22:11+00';
