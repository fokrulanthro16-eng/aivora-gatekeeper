-- Migration: 017_workspace_analytics_rpc
-- Read-only analytics RPCs for admin dashboard and invoice generation.
--
-- Functions:
--   get_workspace_invoice_summary  — full monthly invoice breakdown
--   get_workspace_usage_trend      — N-month rolling usage trend
--   get_platform_stats             — admin: platform-wide aggregate stats

-- ── Invoice summary ───────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.get_workspace_invoice_summary(
    p_workspace_id  uuid,
    p_year          smallint,
    p_month         smallint
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace     public.workspaces%ROWTYPE;
    v_usage         public.workspace_monthly_usage%ROWTYPE;
    v_period_start  timestamptz;
    v_member_usage  jsonb;
    v_alerts        jsonb;
BEGIN
    SELECT * INTO v_workspace FROM public.workspaces WHERE id = p_workspace_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'workspace_not_found');
    END IF;

    SELECT * INTO v_usage
    FROM   public.workspace_monthly_usage
    WHERE  workspace_id = p_workspace_id
    AND    year         = p_year
    AND    month        = p_month;

    -- Build period_start for joining usage_counters
    v_period_start := make_timestamptz(p_year::int, p_month::int, 1, 0, 0, 0, 'UTC');

    -- Per-member usage breakdown
    SELECT jsonb_agg(
        jsonb_build_object(
            'user_uuid',        uc.user_uuid,
            'messages_used',    uc.messages_used,
            'budget_used_usd',  uc.budget_used_usd
        )
        ORDER BY uc.budget_used_usd DESC
    )
    INTO v_member_usage
    FROM   public.usage_counters uc
    JOIN   public.workspace_members wm ON wm.user_uuid = uc.user_uuid
    WHERE  wm.workspace_id = p_workspace_id
    AND    uc.period_start = v_period_start;

    -- Budget alerts triggered this month
    SELECT jsonb_agg(
        jsonb_build_object(
            'threshold_pct',    threshold_pct,
            'triggered_at',     triggered_at,
            'spend_at_trigger', spend_at_trigger
        )
        ORDER BY threshold_pct
    )
    INTO v_alerts
    FROM public.workspace_budget_alerts
    WHERE workspace_id = p_workspace_id
    AND   year         = p_year
    AND   month        = p_month;

    RETURN jsonb_build_object(
        'workspace_id',         p_workspace_id,
        'workspace_name',       v_workspace.name,
        'workspace_slug',       v_workspace.slug,
        'workspace_plan',       v_workspace.plan,
        'period',               to_char(
                                    make_date(p_year::int, p_month::int, 1),
                                    'YYYY-MM'
                                ),
        'budget_usd',           v_workspace.monthly_budget_usd,
        'total_cost_usd',       COALESCE(v_usage.total_cost_usd,   0),
        'total_requests',       COALESCE(v_usage.total_requests,   0),
        'blocked_requests',     COALESCE(v_usage.blocked_requests, 0),
        'total_tokens',         COALESCE(v_usage.total_tokens,     0),
        'last_request_at',      v_usage.last_request_at,
        'budget_utilisation_pct',
            CASE
                WHEN v_workspace.monthly_budget_usd > 0
                THEN ROUND(
                    (COALESCE(v_usage.total_cost_usd, 0) / v_workspace.monthly_budget_usd) * 100,
                    2
                )
                ELSE 0
            END,
        'member_breakdown',     COALESCE(v_member_usage, '[]'::jsonb),
        'budget_alerts',        COALESCE(v_alerts,       '[]'::jsonb)
    );
END;
$$;

-- ── Usage trend ───────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.get_workspace_usage_trend(
    p_workspace_id  uuid,
    p_months        smallint DEFAULT 6
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_rows jsonb;
BEGIN
    SELECT jsonb_agg(
        jsonb_build_object(
            'year',             year,
            'month',            month,
            'period',           lpad(year::text, 4, '0') || '-' || lpad(month::text, 2, '0'),
            'total_cost_usd',   total_cost_usd,
            'total_requests',   total_requests,
            'blocked_requests', blocked_requests,
            'total_tokens',     total_tokens
        )
        ORDER BY year ASC, month ASC
    )
    INTO v_rows
    FROM (
        SELECT * FROM public.workspace_monthly_usage
        WHERE workspace_id = p_workspace_id
        ORDER BY year DESC, month DESC
        LIMIT p_months
    ) sub;

    RETURN COALESCE(v_rows, '[]'::jsonb);
END;
$$;

-- ── Platform stats (admin only) ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.get_platform_stats(
    p_year  smallint DEFAULT NULL,
    p_month smallint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_year  smallint := COALESCE(p_year,  EXTRACT(YEAR  FROM now())::smallint);
    v_month smallint := COALESCE(p_month, EXTRACT(MONTH FROM now())::smallint);
BEGIN
    RETURN (
        SELECT jsonb_build_object(
            'period',                   lpad(v_year::text,4,'0') || '-' || lpad(v_month::text,2,'0'),
            'total_workspaces',         (SELECT COUNT(*) FROM public.workspaces),
            'active_workspaces',        (SELECT COUNT(*) FROM public.workspaces
                                         WHERE is_active = true AND is_suspended = false),
            'suspended_workspaces',     (SELECT COUNT(*) FROM public.workspaces
                                         WHERE is_suspended = true),
            'total_cost_usd',           COALESCE((
                                            SELECT SUM(total_cost_usd)
                                            FROM   public.workspace_monthly_usage
                                            WHERE  year = v_year AND month = v_month
                                        ), 0),
            'total_requests',           COALESCE((
                                            SELECT SUM(total_requests)
                                            FROM   public.workspace_monthly_usage
                                            WHERE  year = v_year AND month = v_month
                                        ), 0),
            'blocked_requests',         COALESCE((
                                            SELECT SUM(blocked_requests)
                                            FROM   public.workspace_monthly_usage
                                            WHERE  year = v_year AND month = v_month
                                        ), 0),
            'workspaces_over_80pct',    (
                                            SELECT COUNT(*)
                                            FROM   public.workspace_budget_alerts
                                            WHERE  year          = v_year
                                            AND    month         = v_month
                                            AND    threshold_pct >= 80
                                        ),
            'active_anomalies',         (
                                            SELECT COUNT(*)
                                            FROM   public.spending_anomalies
                                            WHERE  resolved = false
                                        )
        )
    );
END;
$$;

COMMENT ON FUNCTION public.get_workspace_invoice_summary IS
    'Full monthly invoice: workspace totals, per-member breakdown, '
    'budget alerts. Returns single JSONB object.';

COMMENT ON FUNCTION public.get_workspace_usage_trend IS
    'Rolling N-month usage trend for a workspace, ordered chronologically.';

COMMENT ON FUNCTION public.get_platform_stats IS
    'Admin-only: platform-wide aggregate stats for a given month. '
    'Defaults to current calendar month.';

GRANT EXECUTE ON FUNCTION public.get_workspace_invoice_summary(uuid, smallint, smallint)
    TO service_role;

GRANT EXECUTE ON FUNCTION public.get_workspace_usage_trend(uuid, smallint)
    TO authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.get_platform_stats(smallint, smallint)
    TO service_role;
