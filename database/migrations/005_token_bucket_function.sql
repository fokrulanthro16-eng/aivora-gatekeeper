-- Migration: 005_token_bucket_function
-- Concurrency-safe token bucket gate for API rate limiting.
--
-- Algorithm: continuous-refill token bucket (a.k.a. "leaky bucket as a meter").
--   1. Lock the quota row with SELECT … FOR UPDATE to serialize concurrent calls.
--   2. Compute elapsed seconds since last_refill_at.
--   3. Add elapsed * refill_rate tokens, capped at max_tokens.
--   4. Check the monthly budget hard ceiling.
--   5. If bucket >= request_cost: deduct and allow.
--      Otherwise: leave bucket unchanged and reject.
--   6. Write an api_logs audit row regardless of outcome.
--   7. Return JSONB: { allowed, remaining_tokens, reason }.
--
-- SECURITY DEFINER means the function runs with the owner's privileges so it
-- can write api_logs even when called by an authenticated (non-service-role) user.
-- search_path is pinned to prevent search-path injection.

CREATE OR REPLACE FUNCTION public.process_token_bucket_leak(
    p_user_uuid    uuid,
    p_request_cost integer,
    p_endpoint     text    DEFAULT NULL,
    p_http_method  text    DEFAULT NULL,
    p_request_id   text    DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_quota          public.user_quotas%ROWTYPE;
    v_now            timestamptz;
    v_elapsed_secs   numeric;
    v_refilled       numeric;       -- tokens added by the refill step
    v_bucket_before  numeric;       -- bucket level after refill, before deduction
    v_bucket_after   numeric;       -- bucket level after this request
    v_allowed        boolean;
    v_reason         text;
    v_start_clock    timestamptz;
    v_processing_ms  integer;
BEGIN
    -- Validate input early so we never write a broken log row
    IF p_request_cost IS NULL OR p_request_cost <= 0 THEN
        RAISE EXCEPTION 'request_cost must be a positive integer, got: %', p_request_cost;
    END IF;

    v_start_clock := clock_timestamp();
    v_now         := v_start_clock;

    -- ── 1. Lock the quota row ─────────────────────────────────────────────────
    SELECT *
    INTO   v_quota
    FROM   public.user_quotas
    WHERE  user_uuid = p_user_uuid
    FOR UPDATE;                          -- row-level lock; blocks concurrent calls for this user

    IF NOT FOUND THEN
        -- No quota row — user was never provisioned. Fail open with a clear reason.
        RETURN jsonb_build_object(
            'allowed',           false,
            'remaining_tokens',  0,
            'reason',            'quota_not_found'
        );
    END IF;

    -- ── 2. Check suspension ───────────────────────────────────────────────────
    IF v_quota.is_suspended THEN
        INSERT INTO public.api_logs (
            user_uuid, endpoint, http_method, request_id,
            request_cost, tokens_before, tokens_after,
            allowed, reason, processing_ms
        ) VALUES (
            p_user_uuid, p_endpoint, p_http_method, p_request_id,
            p_request_cost, v_quota.current_tokens, v_quota.current_tokens,
            false, 'suspended',
            EXTRACT(MILLISECONDS FROM (clock_timestamp() - v_start_clock))::integer
        );

        RETURN jsonb_build_object(
            'allowed',           false,
            'remaining_tokens',  v_quota.current_tokens,
            'reason',            'suspended'
        );
    END IF;

    -- ── 3. Roll the billing period if calendar month changed ──────────────────
    IF date_trunc('month', v_now) > date_trunc('month', v_quota.period_start) THEN
        UPDATE public.user_quotas
        SET    period_start            = date_trunc('month', v_now),
               tokens_used_this_period = 0
        WHERE  user_uuid = p_user_uuid;

        v_quota.period_start            := date_trunc('month', v_now);
        v_quota.tokens_used_this_period := 0;
    END IF;

    -- ── 4. Continuous refill ──────────────────────────────────────────────────
    v_elapsed_secs  := EXTRACT(EPOCH FROM (v_now - v_quota.last_refill_at));
    v_refilled      := v_elapsed_secs * v_quota.refill_rate;
    v_bucket_before := LEAST(v_quota.current_tokens + v_refilled, v_quota.max_tokens);

    -- ── 5. Monthly hard ceiling ───────────────────────────────────────────────
    IF (v_quota.tokens_used_this_period + p_request_cost) > v_quota.monthly_token_budget THEN
        v_allowed        := false;
        v_reason         := 'monthly_budget_exceeded';
        v_bucket_after   := v_bucket_before;   -- bucket untouched on rejection

        UPDATE public.user_quotas
        SET    current_tokens  = v_bucket_before,
               last_refill_at  = v_now
        WHERE  user_uuid = p_user_uuid;

    -- ── 6. Token bucket gate ──────────────────────────────────────────────────
    ELSIF v_bucket_before >= p_request_cost THEN
        v_allowed      := true;
        v_reason       := 'allowed';
        v_bucket_after := v_bucket_before - p_request_cost;

        UPDATE public.user_quotas
        SET    current_tokens          = v_bucket_after,
               last_refill_at          = v_now,
               tokens_used_this_period = tokens_used_this_period + p_request_cost
        WHERE  user_uuid = p_user_uuid;

    ELSE
        v_allowed      := false;
        v_reason       := 'insufficient_tokens';
        v_bucket_after := v_bucket_before;     -- bucket untouched on rejection

        UPDATE public.user_quotas
        SET    current_tokens = v_bucket_before,
               last_refill_at = v_now
        WHERE  user_uuid = p_user_uuid;
    END IF;

    -- ── 7. Audit log (always written) ─────────────────────────────────────────
    v_processing_ms := EXTRACT(MILLISECONDS FROM (clock_timestamp() - v_start_clock))::integer;

    INSERT INTO public.api_logs (
        user_uuid,
        endpoint,
        http_method,
        request_id,
        request_cost,
        tokens_before,
        tokens_after,
        allowed,
        reason,
        processing_ms
    ) VALUES (
        p_user_uuid,
        p_endpoint,
        p_http_method,
        p_request_id,
        p_request_cost,
        v_bucket_before,
        v_bucket_after,
        v_allowed,
        v_reason,
        v_processing_ms
    );

    -- ── 8. Return decision ────────────────────────────────────────────────────
    RETURN jsonb_build_object(
        'allowed',           v_allowed,
        'remaining_tokens',  v_bucket_after,
        'reason',            v_reason
    );

EXCEPTION
    -- Propagate unexpected errors as JSONB so callers can handle gracefully.
    WHEN OTHERS THEN
        RAISE WARNING 'process_token_bucket_leak error for user %: %', p_user_uuid, SQLERRM;
        RETURN jsonb_build_object(
            'allowed',           false,
            'remaining_tokens',  0,
            'reason',            'internal_error'
        );
END;
$$;

COMMENT ON FUNCTION public.process_token_bucket_leak IS
    'Concurrency-safe token bucket gate. Locks the user_quotas row, refills tokens based on
     elapsed time, enforces the monthly budget ceiling, deducts tokens atomically, writes an
     audit row to api_logs, and returns { allowed, remaining_tokens, reason }.';

-- Grant execute to authenticated users so they can call it via the Supabase client.
-- The function itself enforces that users can only consume their own quota via p_user_uuid.
GRANT EXECUTE ON FUNCTION public.process_token_bucket_leak(uuid, integer, text, text, text)
    TO authenticated, service_role;
