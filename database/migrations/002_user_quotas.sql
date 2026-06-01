-- Migration: 002_user_quotas
-- Per-user token bucket state, linked to auth.users and a billing tier.

CREATE TABLE IF NOT EXISTS public.user_quotas (
    id                      uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_uuid               uuid            NOT NULL UNIQUE
                                            REFERENCES auth.users (id) ON DELETE CASCADE,
    billing_tier_id         smallint        NOT NULL
                                            REFERENCES public.billing_tiers (id),

    -- Live bucket state (updated atomically by process_token_bucket_leak)
    current_tokens          numeric(18, 4)  NOT NULL DEFAULT 0,
    last_refill_at          timestamptz     NOT NULL DEFAULT now(),

    -- Mirrors the tier values at the time the user was provisioned so that
    -- a tier change can be applied without a migration (just update these columns).
    max_tokens              integer         NOT NULL,
    refill_rate             numeric(12, 4)  NOT NULL,
    monthly_token_budget    bigint          NOT NULL,

    -- Monthly rolling window
    period_start            timestamptz     NOT NULL DEFAULT date_trunc('month', now()),
    tokens_used_this_period bigint          NOT NULL DEFAULT 0,

    -- Administrative
    is_suspended            boolean         NOT NULL DEFAULT false,
    suspension_reason       text,
    notes                   text,

    created_at              timestamptz     NOT NULL DEFAULT now(),
    updated_at              timestamptz     NOT NULL DEFAULT now(),

    CONSTRAINT chk_current_tokens_non_negative CHECK (current_tokens >= 0),
    CONSTRAINT chk_tokens_used_non_negative    CHECK (tokens_used_this_period >= 0)
);

COMMENT ON TABLE  public.user_quotas IS 'Per-user token bucket state. One row per user, updated by process_token_bucket_leak().';
COMMENT ON COLUMN public.user_quotas.current_tokens          IS 'Tokens currently available in the bucket. Updated atomically via FOR UPDATE lock.';
COMMENT ON COLUMN public.user_quotas.last_refill_at          IS 'Timestamp of last refill calculation. Used to compute elapsed time for continuous refill.';
COMMENT ON COLUMN public.user_quotas.tokens_used_this_period IS 'Cumulative tokens consumed since period_start. Checked against monthly_token_budget.';
COMMENT ON COLUMN public.user_quotas.period_start            IS 'Start of the current billing month window. Reset monthly to truncate period counters.';

CREATE TRIGGER trg_user_quotas_updated_at
    BEFORE UPDATE ON public.user_quotas
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Helper: provision a new quota row copying defaults from the billing tier
CREATE OR REPLACE FUNCTION public.provision_user_quota(
    p_user_uuid        uuid,
    p_billing_tier_id  smallint DEFAULT 1  -- 1 = Free
)
RETURNS public.user_quotas
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_tier  public.billing_tiers%ROWTYPE;
    v_quota public.user_quotas%ROWTYPE;
BEGIN
    SELECT * INTO v_tier FROM public.billing_tiers WHERE id = p_billing_tier_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'billing_tier_id % not found', p_billing_tier_id;
    END IF;

    INSERT INTO public.user_quotas (
        user_uuid,
        billing_tier_id,
        current_tokens,
        max_tokens,
        refill_rate,
        monthly_token_budget
    ) VALUES (
        p_user_uuid,
        p_billing_tier_id,
        v_tier.max_tokens,          -- start with a full bucket
        v_tier.max_tokens,
        v_tier.refill_rate,
        v_tier.monthly_token_budget
    )
    ON CONFLICT (user_uuid) DO NOTHING
    RETURNING * INTO v_quota;

    RETURN v_quota;
END;
$$;

COMMENT ON FUNCTION public.provision_user_quota IS
    'Idempotent: creates a user_quotas row from billing tier defaults. Call after user sign-up.';
