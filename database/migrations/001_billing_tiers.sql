-- Migration: 001_billing_tiers
-- Creates the billing_tiers lookup table that drives per-tier rate limits.

CREATE TABLE IF NOT EXISTS public.billing_tiers (
    id                   smallint        PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    name                 text            NOT NULL UNIQUE,
    -- Token bucket parameters
    max_tokens           integer         NOT NULL CHECK (max_tokens > 0),
    refill_rate          numeric(12, 4)  NOT NULL CHECK (refill_rate > 0),  -- tokens / second
    -- Monthly budget cap (hard ceiling independent of bucket state)
    monthly_token_budget bigint          NOT NULL CHECK (monthly_token_budget > 0),
    -- Pricing
    price_usd_cents      integer         NOT NULL DEFAULT 0 CHECK (price_usd_cents >= 0),
    -- Soft-delete / ordering
    is_active            boolean         NOT NULL DEFAULT true,
    display_order        smallint        NOT NULL DEFAULT 0,

    created_at           timestamptz     NOT NULL DEFAULT now(),
    updated_at           timestamptz     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public.billing_tiers IS 'Lookup table of subscription tiers that define token bucket parameters.';
COMMENT ON COLUMN public.billing_tiers.max_tokens           IS 'Bucket capacity — maximum tokens a user may accumulate before refill stops.';
COMMENT ON COLUMN public.billing_tiers.refill_rate          IS 'Continuous refill rate in tokens per second.';
COMMENT ON COLUMN public.billing_tiers.monthly_token_budget IS 'Hard monthly ceiling on total tokens consumed. Enforced independently of the bucket.';

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_billing_tiers_updated_at
    BEFORE UPDATE ON public.billing_tiers
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Seed: Free / Pro / Enterprise
INSERT INTO public.billing_tiers
    (name,         max_tokens,   refill_rate, monthly_token_budget, price_usd_cents, display_order)
VALUES
    ('Free',       10000,        1.0,         500000,               0,               1),
    ('Pro',        100000,       10.0,        10000000,             2900,            2),
    ('Enterprise', 1000000,      100.0,       500000000,            29900,           3)
ON CONFLICT (name) DO NOTHING;
