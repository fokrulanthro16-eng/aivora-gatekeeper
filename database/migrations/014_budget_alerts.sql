-- Migration: 014_budget_alerts
-- Workspace budget threshold alert events.
-- One row per (workspace_id, year, month, threshold_pct) — fires once per threshold per month.
-- Inserted by _fire_budget_alerts() inside workspace_check_and_consume_usage().

CREATE TABLE IF NOT EXISTS public.workspace_budget_alerts (
    id                  uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid            NOT NULL
                                        REFERENCES public.workspaces (id) ON DELETE CASCADE,
    year                smallint        NOT NULL,
    month               smallint        NOT NULL,

    -- Which threshold triggered (50%, 80%, 95%, 100%)
    threshold_pct       smallint        NOT NULL CHECK (threshold_pct IN (50, 80, 95, 100)),

    -- Snapshot at time of trigger
    spend_at_trigger    numeric(12, 4)  NOT NULL CHECK (spend_at_trigger >= 0),
    budget_usd          numeric(12, 4)  NOT NULL CHECK (budget_usd > 0),

    triggered_at        timestamptz     NOT NULL DEFAULT now(),

    -- Idempotent: one alert per workspace per month per threshold
    UNIQUE (workspace_id, year, month, threshold_pct)
);

COMMENT ON TABLE public.workspace_budget_alerts IS
    'Budget threshold events. Idempotent: each threshold fires at most once per '
    'workspace per calendar month. Inserted by _fire_budget_alerts().';

COMMENT ON COLUMN public.workspace_budget_alerts.threshold_pct IS
    'Budget consumption % that triggered this alert: 50, 80, 95, or 100.';

CREATE INDEX IF NOT EXISTS idx_budget_alerts_workspace_period
    ON public.workspace_budget_alerts (workspace_id, year DESC, month DESC);

CREATE INDEX IF NOT EXISTS idx_budget_alerts_recent
    ON public.workspace_budget_alerts (triggered_at DESC);

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.workspace_budget_alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "budget_alerts_select_admin"
    ON public.workspace_budget_alerts FOR SELECT TO authenticated
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_uuid = auth.uid() AND role IN ('owner', 'admin')
        )
    );

CREATE POLICY "budget_alerts_all_service_role"
    ON public.workspace_budget_alerts FOR ALL TO service_role
    USING (true) WITH CHECK (true);
