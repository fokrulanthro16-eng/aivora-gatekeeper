-- Migration: 016_workspace_quota_rpc
-- workspace_check_and_consume_usage(): hierarchical quota gate.
--
-- Enforcement order (hard stops, left-to-right):
--   1. Workspace monthly USD budget cap (workspace_monthly_usage aggregate)
--   2. User monthly message limit       (usage_counters.messages_used)
--   3. User monthly dollar budget       (usage_counters.budget_used_usd)
--   4. User token bucket                (user_quotas.current_tokens)
--
-- If the calling user has no workspace_id, steps 1 & aggregation are skipped
-- and the function is equivalent to check_and_consume_ai_usage().
--
-- Returns the same JSON shape as check_and_consume_ai_usage plus workspace fields:
--   workspace_id              uuid | null
--   workspace_budget_usd      numeric | null   (null when no workspace)
--   workspace_spend_usd       numeric          (0 when no workspace)
--   workspace_remaining_usd   numeric | null   (null when no workspace)
--
-- Reason codes (workspace-level, in addition to existing user-level codes):
--   workspace_suspended    workspace.is_suspended = true or is_active = false
--   workspace_budget_exceeded  workspace spend >= monthly_budget_usd
--
-- Note on workspace budget atomicity:
--   The workspace budget check reads workspace_monthly_usage and the deduction
--   happens later in the same plpgsql call. Concurrent requests may both pass
--   the check before either updates the aggregate — this bounds over-spend to
--   at most (concurrency × max_request_cost), which is acceptable for SaaS
--   billing. For stricter enforcement, add a workspace-level SELECT … FOR UPDATE.

CREATE OR REPLACE FUNCTION public.workspace_check_and_consume_usage(
    p_user_uuid         uuid,
    p_provider          text,
    p_model             text,
    p_estimated_tokens  integer,
    p_estimated_cost    numeric
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_ws_id             uuid;
    v_workspace         public.workspaces%ROWTYPE;
    v_ws_spend          numeric  := 0;
    v_ws_remaining      numeric;
    v_now               timestamptz := clock_timestamp();
    v_year              smallint    := EXTRACT(YEAR  FROM v_now)::smallint;
    v_month             smallint    := EXTRACT(MONTH FROM v_now)::smallint;
    v_user_result       jsonb;
BEGIN
    -- ── 1. Resolve workspace for this user ────────────────────────────────────
    SELECT workspace_id
    INTO   v_ws_id
    FROM   public.user_quotas
    WHERE  user_uuid = p_user_uuid;

    -- ── 2. Workspace-level checks (skipped when no workspace) ─────────────────
    IF v_ws_id IS NOT NULL THEN

        SELECT * INTO v_workspace
        FROM   public.workspaces
        WHERE  id        = v_ws_id
        AND    is_active = true
        AND    is_suspended = false;

        IF NOT FOUND THEN
            RETURN jsonb_build_object(
                'allowed',              false,
                'reason',               'workspace_suspended',
                'remaining_messages',   0,
                'remaining_budget_usd', 0,
                'estimated_cost',       p_estimated_cost,
                'provider',             p_provider,
                'model',                p_model,
                'workspace_id',         v_ws_id,
                'workspace_spend_usd',  0,
                'workspace_remaining_usd', 0
            );
        END IF;

        -- Current month workspace spend
        SELECT COALESCE(total_cost_usd, 0)
        INTO   v_ws_spend
        FROM   public.workspace_monthly_usage
        WHERE  workspace_id = v_ws_id
        AND    year         = v_year
        AND    month        = v_month;

        v_ws_spend := COALESCE(v_ws_spend, 0);

        -- Hard workspace budget cap
        IF (v_ws_spend + p_estimated_cost) > v_workspace.monthly_budget_usd THEN
            v_ws_remaining := GREATEST(0, v_workspace.monthly_budget_usd - v_ws_spend);

            -- Count blocked workspace request
            INSERT INTO public.workspace_monthly_usage
                (workspace_id, year, month, blocked_requests, updated_at)
            VALUES
                (v_ws_id, v_year, v_month, 1, v_now)
            ON CONFLICT (workspace_id, year, month) DO UPDATE SET
                blocked_requests = workspace_monthly_usage.blocked_requests + 1,
                updated_at       = EXCLUDED.updated_at;

            RETURN jsonb_build_object(
                'allowed',                  false,
                'reason',                   'workspace_budget_exceeded',
                'remaining_messages',       0,
                'remaining_budget_usd',     v_ws_remaining,
                'estimated_cost',           p_estimated_cost,
                'provider',                 p_provider,
                'model',                    p_model,
                'workspace_id',             v_ws_id,
                'workspace_budget_usd',     v_workspace.monthly_budget_usd,
                'workspace_spend_usd',      v_ws_spend,
                'workspace_remaining_usd',  v_ws_remaining
            );
        END IF;
    END IF;

    -- ── 3. User-level quota check (message limit + dollar budget + token bucket)
    v_user_result := public.check_and_consume_ai_usage(
        p_user_uuid,
        p_provider,
        p_model,
        p_estimated_tokens,
        p_estimated_cost
    );

    -- ── 4. On allow: update workspace aggregate + fire threshold alerts ────────
    IF (v_user_result->>'allowed')::boolean AND v_ws_id IS NOT NULL THEN
        INSERT INTO public.workspace_monthly_usage
            (workspace_id, year, month,
             total_requests, total_tokens, total_cost_usd,
             last_request_at, updated_at)
        VALUES
            (v_ws_id, v_year, v_month,
             1, p_estimated_tokens, p_estimated_cost,
             v_now, v_now)
        ON CONFLICT (workspace_id, year, month) DO UPDATE SET
            total_requests  = workspace_monthly_usage.total_requests + 1,
            total_tokens    = workspace_monthly_usage.total_tokens   + p_estimated_tokens,
            total_cost_usd  = workspace_monthly_usage.total_cost_usd + p_estimated_cost,
            last_request_at = EXCLUDED.last_request_at,
            updated_at      = EXCLUDED.updated_at;

        v_ws_spend := v_ws_spend + p_estimated_cost;

        -- Fire threshold alerts (idempotent)
        IF v_workspace.monthly_budget_usd > 0 THEN
            PERFORM public._fire_budget_alerts(
                v_ws_id, v_year, v_month,
                v_ws_spend, v_workspace.monthly_budget_usd
            );
        END IF;
    END IF;

    -- On user-level block: count blocked requests for workspace analytics
    IF NOT (v_user_result->>'allowed')::boolean AND v_ws_id IS NOT NULL THEN
        INSERT INTO public.workspace_monthly_usage
            (workspace_id, year, month, blocked_requests, updated_at)
        VALUES
            (v_ws_id, v_year, v_month, 1, v_now)
        ON CONFLICT (workspace_id, year, month) DO UPDATE SET
            blocked_requests = workspace_monthly_usage.blocked_requests + 1,
            updated_at       = EXCLUDED.updated_at;
    END IF;

    -- ── 5. Attach workspace context and return ─────────────────────────────────
    RETURN v_user_result || jsonb_build_object(
        'workspace_id',
            v_ws_id,
        'workspace_budget_usd',
            CASE WHEN v_ws_id IS NOT NULL THEN v_workspace.monthly_budget_usd ELSE NULL END,
        'workspace_spend_usd',
            v_ws_spend,
        'workspace_remaining_usd',
            CASE
                WHEN v_ws_id IS NOT NULL
                THEN GREATEST(0, v_workspace.monthly_budget_usd - v_ws_spend)
                ELSE NULL
            END
    );

EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'workspace_check_and_consume_usage error for user %: %',
            p_user_uuid, SQLERRM;
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'internal_error',
            'remaining_messages',   0,
            'remaining_budget_usd', 0,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model,
            'workspace_id',         v_ws_id,
            'workspace_spend_usd',  0
        );
END;
$$;

-- ── Budget alert threshold helper ─────────────────────────────────────────────
-- Called inside workspace_check_and_consume_usage after every successful deduction.
-- Inserts an alert row for each threshold that has been crossed this month.
-- The UNIQUE constraint on workspace_budget_alerts makes this idempotent.

CREATE OR REPLACE FUNCTION public._fire_budget_alerts(
    p_workspace_id  uuid,
    p_year          smallint,
    p_month         smallint,
    p_current_spend numeric,
    p_budget_usd    numeric
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_pct       numeric := (p_current_spend / p_budget_usd) * 100;
    v_threshold smallint;
BEGIN
    FOREACH v_threshold IN ARRAY ARRAY[50, 80, 95, 100]::smallint[] LOOP
        IF v_pct >= v_threshold THEN
            INSERT INTO public.workspace_budget_alerts
                (workspace_id, year, month, threshold_pct,
                 spend_at_trigger, budget_usd)
            VALUES
                (p_workspace_id, p_year, p_month, v_threshold,
                 p_current_spend, p_budget_usd)
            ON CONFLICT (workspace_id, year, month, threshold_pct)
                DO NOTHING;
        END IF;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION public.workspace_check_and_consume_usage IS
    'Hierarchical quota gate: workspace budget → user message limit → '
    'user dollar budget → user token bucket. Drop-in extension of '
    'check_and_consume_ai_usage that adds workspace-level enforcement '
    'and real-time aggregation. Returns same JSON shape plus workspace fields.';

COMMENT ON FUNCTION public._fire_budget_alerts IS
    'Idempotent threshold event recorder. Called after each successful deduction. '
    'Fires alerts at 50/80/95/100% of workspace monthly_budget_usd.';

GRANT EXECUTE ON FUNCTION public.workspace_check_and_consume_usage(uuid, text, text, integer, numeric)
    TO authenticated, service_role;
