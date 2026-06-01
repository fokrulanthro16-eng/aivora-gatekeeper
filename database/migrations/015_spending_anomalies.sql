-- Migration: 015_spending_anomalies
-- Detected spending anomalies per workspace.
-- Inserted by the Python anomaly_detector service after each workspace usage update.
-- Anomalies can be resolved (resolved=true) by admin action.

CREATE TABLE IF NOT EXISTS public.spending_anomalies (
    id                  uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid            NOT NULL
                                        REFERENCES public.workspaces (id) ON DELETE CASCADE,

    -- Anomaly classification
    anomaly_type        text            NOT NULL
                                        CHECK (anomaly_type IN (
                                            'spend_spike',        -- daily rate >> rolling baseline
                                            'budget_trajectory',  -- projected month-end > budget
                                            'rapid_acceleration'  -- spend rate growing >50% DoD
                                        )),
    severity            text            NOT NULL DEFAULT 'medium'
                                        CHECK (severity IN ('low', 'medium', 'high', 'critical')),

    -- Numeric evidence (units depend on anomaly_type; documented in description)
    current_value       numeric(16, 6)  NOT NULL,  -- e.g. current daily rate USD
    baseline_value      numeric(16, 6)  NOT NULL,  -- e.g. 7-day rolling average daily rate USD
    deviation_pct       numeric(8, 2)   NOT NULL,  -- (current / baseline - 1) × 100

    -- Human-readable explanation
    description         text            NOT NULL,

    -- Resolution
    resolved            boolean         NOT NULL DEFAULT false,
    resolved_at         timestamptz,

    detected_at         timestamptz     NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.spending_anomalies IS
    'Spending anomalies detected by the Python anomaly_detector service. '
    'Inserted as background tasks after workspace usage updates. '
    'Anomalies are additive — the same pattern may produce multiple rows '
    'if not resolved. Use resolved=true to suppress further alerting.';

COMMENT ON COLUMN public.spending_anomalies.current_value IS
    'spend_spike: today USD/day rate. '
    'budget_trajectory: projected month-end USD. '
    'rapid_acceleration: today USD/day rate.';

COMMENT ON COLUMN public.spending_anomalies.baseline_value IS
    'spend_spike: rolling 3-month average USD/day. '
    'budget_trajectory: workspace monthly_budget_usd. '
    'rapid_acceleration: yesterday USD/day rate.';

CREATE INDEX IF NOT EXISTS idx_anomalies_workspace_recent
    ON public.spending_anomalies (workspace_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_anomalies_unresolved
    ON public.spending_anomalies (workspace_id, detected_at DESC)
    WHERE resolved = false;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.spending_anomalies ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anomalies_select_admin"
    ON public.spending_anomalies FOR SELECT TO authenticated
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_uuid = auth.uid() AND role IN ('owner', 'admin')
        )
    );

CREATE POLICY "anomalies_all_service_role"
    ON public.spending_anomalies FOR ALL TO service_role
    USING (true) WITH CHECK (true);
