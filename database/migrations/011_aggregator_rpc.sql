-- Migration: 011_aggregator_rpc
-- Core AI aggregator quota function.
--
-- check_and_consume_ai_usage() is the single atomic gate that every
-- /proxy-openrouter call passes through before reaching the provider.
--
-- Locking order (always preserved to prevent deadlocks):
--   1. user_quotas        FOR UPDATE  (token bucket state)
--   2. usage_counters     FOR UPDATE  (message + dollar counters)
--
-- Return JSON shape:
--   {
--     "allowed":               bool,
--     "reason":                text,      -- see reason codes below
--     "remaining_messages":    integer,
--     "remaining_budget_usd":  numeric,
--     "estimated_cost":        numeric,
--     "provider":              text,
--     "model":                 text
--   }
--
-- Reason codes:
--   allowed                        All checks passed
--   quota_not_found                No user_quotas row — call provision_user_quota() first
--   account_suspended              is_suspended = true on user_quotas
--   monthly_message_limit_exceeded messages_used >= tier.monthly_message_limit
--   monthly_budget_exceeded        budget_used_usd + p_estimated_cost > tier.monthly_budget_usd
--   token_bucket_exhausted         current_tokens < p_estimated_tokens

CREATE OR REPLACE FUNCTION public.check_and_consume_ai_usage(
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
    v_quota             public.user_quotas%ROWTYPE;
    v_tier              public.billing_tiers%ROWTYPE;
    v_usage             public.usage_counters%ROWTYPE;
    v_now               timestamptz := clock_timestamp();
    v_period_start      timestamptz := date_trunc('month', v_now);
    v_elapsed_secs      numeric;
    v_bucket_before     numeric;
    v_bucket_after      numeric;
    v_remaining_msgs    integer;
    v_remaining_budget  numeric;
BEGIN
    -- ── 1. Lock user quota row ────────────────────────────────────────────────
    SELECT * INTO v_quota
    FROM   public.user_quotas
    WHERE  user_uuid = p_user_uuid
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'quota_not_found',
            'remaining_messages',   0,
            'remaining_budget_usd', 0,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
    END IF;

    -- ── 2. Check suspension ───────────────────────────────────────────────────
    IF v_quota.is_suspended THEN
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'account_suspended',
            'remaining_messages',   0,
            'remaining_budget_usd', 0,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
    END IF;

    -- ── 3. Resolve active tier ────────────────────────────────────────────────
    -- Prefer an active subscription tier; fall back to user_quotas.billing_tier_id.
    SELECT bt.* INTO v_tier
    FROM   public.subscriptions s
    JOIN   public.billing_tiers bt ON bt.id = s.tier_id
    WHERE  s.user_uuid = p_user_uuid
    AND    s.status    IN ('active', 'trialing')
    ORDER  BY s.created_at DESC
    LIMIT  1;

    IF NOT FOUND THEN
        SELECT * INTO v_tier
        FROM   public.billing_tiers
        WHERE  id = v_quota.billing_tier_id;
    END IF;

    -- ── 4. Get or create usage counter for this billing period ────────────────
    -- INSERT … ON CONFLICT is safe here: user_quotas is already locked, so no
    -- concurrent call for this user can reach this point simultaneously.
    INSERT INTO public.usage_counters (user_uuid, period_start)
    VALUES (p_user_uuid, v_period_start)
    ON CONFLICT (user_uuid, period_start) DO NOTHING;

    SELECT * INTO v_usage
    FROM   public.usage_counters
    WHERE  user_uuid     = p_user_uuid
    AND    period_start  = v_period_start
    FOR UPDATE;

    -- ── 5. Check monthly message limit ────────────────────────────────────────
    IF v_tier.monthly_message_limit IS NOT NULL
       AND v_usage.messages_used >= v_tier.monthly_message_limit
    THEN
        v_remaining_budget := GREATEST(
            0,
            COALESCE(v_tier.monthly_budget_usd, 999999) - v_usage.budget_used_usd
        );
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'monthly_message_limit_exceeded',
            'remaining_messages',   0,
            'remaining_budget_usd', v_remaining_budget,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
    END IF;

    -- ── 6. Check monthly dollar budget ───────────────────────────────────────
    IF v_tier.monthly_budget_usd IS NOT NULL
       AND (v_usage.budget_used_usd + p_estimated_cost) > v_tier.monthly_budget_usd
    THEN
        v_remaining_msgs := CASE
            WHEN v_tier.monthly_message_limit IS NULL THEN 999999
            ELSE GREATEST(0, v_tier.monthly_message_limit - v_usage.messages_used)
        END;
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'monthly_budget_exceeded',
            'remaining_messages',   v_remaining_msgs,
            'remaining_budget_usd', GREATEST(0, v_tier.monthly_budget_usd - v_usage.budget_used_usd),
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
    END IF;

    -- ── 7. Token bucket: continuous refill + balance check ───────────────────
    v_elapsed_secs  := EXTRACT(EPOCH FROM (v_now - v_quota.last_refill_at));
    v_bucket_before := LEAST(
        v_quota.current_tokens + (v_elapsed_secs * v_quota.refill_rate),
        v_quota.max_tokens
    );

    IF v_bucket_before < p_estimated_tokens THEN
        -- Update refill timestamp even on rejection so the next call starts fresh
        UPDATE public.user_quotas
        SET    current_tokens = v_bucket_before,
               last_refill_at = v_now
        WHERE  user_uuid = p_user_uuid;

        v_remaining_msgs := CASE
            WHEN v_tier.monthly_message_limit IS NULL THEN 999999
            ELSE GREATEST(0, v_tier.monthly_message_limit - v_usage.messages_used)
        END;
        v_remaining_budget := CASE
            WHEN v_tier.monthly_budget_usd IS NULL THEN 999999
            ELSE GREATEST(0, v_tier.monthly_budget_usd - v_usage.budget_used_usd)
        END;

        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'token_bucket_exhausted',
            'remaining_messages',   v_remaining_msgs,
            'remaining_budget_usd', v_remaining_budget,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
    END IF;

    -- ── 8. All checks passed — deduct atomically ──────────────────────────────
    v_bucket_after := v_bucket_before - p_estimated_tokens;

    UPDATE public.user_quotas
    SET    current_tokens          = v_bucket_after,
           last_refill_at          = v_now,
           tokens_used_this_period = tokens_used_this_period + p_estimated_tokens
    WHERE  user_uuid = p_user_uuid;

    UPDATE public.usage_counters
    SET    messages_used   = messages_used   + 1,
           budget_used_usd = budget_used_usd + p_estimated_cost,
           last_updated_at = v_now
    WHERE  user_uuid    = p_user_uuid
    AND    period_start = v_period_start;

    -- ── 9. Audit log ──────────────────────────────────────────────────────────
    INSERT INTO public.api_logs (
        user_uuid, endpoint, http_method,
        request_cost, tokens_before, tokens_after,
        allowed, reason
    ) VALUES (
        p_user_uuid, '/v1/aggregator/proxy-openrouter', 'POST',
        p_estimated_tokens, v_bucket_before, v_bucket_after,
        true, 'allowed'
    );

    -- ── 10. Return success payload ────────────────────────────────────────────
    v_remaining_msgs := CASE
        WHEN v_tier.monthly_message_limit IS NULL THEN 999999
        ELSE GREATEST(0, v_tier.monthly_message_limit - v_usage.messages_used - 1)
    END;
    v_remaining_budget := CASE
        WHEN v_tier.monthly_budget_usd IS NULL THEN 999999
        ELSE GREATEST(0, v_tier.monthly_budget_usd - v_usage.budget_used_usd - p_estimated_cost)
    END;

    RETURN jsonb_build_object(
        'allowed',              true,
        'reason',               'allowed',
        'remaining_messages',   v_remaining_msgs,
        'remaining_budget_usd', v_remaining_budget,
        'estimated_cost',       p_estimated_cost,
        'provider',             p_provider,
        'model',                p_model
    );

EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'check_and_consume_ai_usage error for user %: %', p_user_uuid, SQLERRM;
        RETURN jsonb_build_object(
            'allowed',              false,
            'reason',               'internal_error',
            'remaining_messages',   0,
            'remaining_budget_usd', 0,
            'estimated_cost',       p_estimated_cost,
            'provider',             p_provider,
            'model',                p_model
        );
END;
$$;

COMMENT ON FUNCTION public.check_and_consume_ai_usage IS
    'Atomic AI usage gate. Checks subscription tier limits, dollar budget, and token '
    'bucket. Deducts all counters in a single transaction. Call before forwarding to '
    'any LLM provider. Returns { allowed, reason, remaining_messages, remaining_budget_usd, '
    'estimated_cost, provider, model }.';

-- Grant execute to authenticated users and service role
GRANT EXECUTE ON FUNCTION public.check_and_consume_ai_usage(uuid, text, text, integer, numeric)
    TO authenticated, service_role;
