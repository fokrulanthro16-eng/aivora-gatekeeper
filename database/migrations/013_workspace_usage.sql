-- Migration: 013_workspace_usage
-- Monthly usage aggregation per workspace.
-- Updated atomically by workspace_check_and_consume_usage() on every allowed request.
-- Provides real-time aggregate analytics without scanning api_logs.

CREATE TABLE IF NOT EXISTS public.workspace_monthly_usage (
    workspace_id        uuid            NOT NULL
                                        REFERENCES public.workspaces (id) ON DELETE CASCADE,
    year                smallint        NOT NULL CHECK (year BETWEEN 2024 AND 2099),
    month               smallint        NOT NULL CHECK (month BETWEEN 1 AND 12),

    -- Request counters
    total_requests      bigint          NOT NULL DEFAULT 0 CHECK (total_requests >= 0),
    blocked_requests    bigint          NOT NULL DEFAULT 0 CHECK (blocked_requests >= 0),

    -- Token + cost aggregates
    total_tokens        bigint          NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    total_cost_usd      numeric(16, 8)  NOT NULL DEFAULT 0 CHECK (total_cost_usd >= 0),

    -- Recency
    last_request_at     timestamptz,
    updated_at          timestamptz     NOT NULL DEFAULT now(),

    PRIMARY KEY (workspace_id, year, month)
);

COMMENT ON TABLE public.workspace_monthly_usage IS
    'Workspace-level monthly aggregates. Updated atomically by '
    'workspace_check_and_consume_usage(). Never UPDATE manually — '
    'use the RPC to maintain consistency with usage_counters.';

COMMENT ON COLUMN public.workspace_monthly_usage.blocked_requests IS
    'Requests rejected at the workspace budget gate (workspace_budget_exceeded). '
    'Does not include requests blocked at the user quota level.';

-- Fast lookup for analytics and invoice generation
CREATE INDEX IF NOT EXISTS idx_wmu_recent
    ON public.workspace_monthly_usage (year DESC, month DESC);

-- High-spend workspace detection for anomaly scanning
CREATE INDEX IF NOT EXISTS idx_wmu_high_spend
    ON public.workspace_monthly_usage (total_cost_usd DESC)
    WHERE total_cost_usd > 0;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.workspace_monthly_usage ENABLE ROW LEVEL SECURITY;

CREATE POLICY "wmu_select_member"
    ON public.workspace_monthly_usage FOR SELECT TO authenticated
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_uuid = auth.uid()
        )
    );

CREATE POLICY "wmu_all_service_role"
    ON public.workspace_monthly_usage FOR ALL TO service_role
    USING (true) WITH CHECK (true);
