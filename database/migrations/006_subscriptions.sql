-- Migration: 006_subscriptions
-- Extends billing_tiers with AI aggregator limits and adds the subscriptions
-- table that Polar.sh webhook events will write into.

-- ── Extend billing_tiers with message + dollar limits ─────────────────────────

ALTER TABLE public.billing_tiers
    ADD COLUMN IF NOT EXISTS monthly_message_limit integer,        -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS monthly_budget_usd    numeric(10, 4); -- NULL = unlimited

COMMENT ON COLUMN public.billing_tiers.monthly_message_limit IS
    'Maximum OpenRouter API calls per calendar month. NULL = unlimited.';
COMMENT ON COLUMN public.billing_tiers.monthly_budget_usd IS
    'Maximum estimated spend in USD per calendar month. NULL = unlimited.';

-- Back-fill limits for the three default tiers
UPDATE public.billing_tiers SET
    monthly_message_limit = 50,
    monthly_budget_usd    = 0.50
WHERE name = 'Free';

UPDATE public.billing_tiers SET
    monthly_message_limit = 1000,
    monthly_budget_usd    = 20.00
WHERE name = 'Pro';

UPDATE public.billing_tiers SET
    monthly_message_limit = NULL,   -- unlimited
    monthly_budget_usd    = 500.00
WHERE name = 'Enterprise';

-- ── Subscriptions table ───────────────────────────────────────────────────────
-- One row per user.  Created / updated by Polar.sh webhook events.
-- Free-tier users may have no row here; the fallback is user_quotas.billing_tier_id.

CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_uuid               uuid        NOT NULL UNIQUE
                                        REFERENCES auth.users (id) ON DELETE CASCADE,
    tier_id                 smallint    NOT NULL
                                        REFERENCES public.billing_tiers (id),
    -- Polar.sh identifiers (NULL for manually provisioned rows)
    polar_subscription_id   text        UNIQUE,
    polar_customer_id       text,
    -- Lifecycle
    status                  text        NOT NULL DEFAULT 'active'
                                        CHECK (status IN (
                                            'active', 'trialing', 'past_due',
                                            'cancelled', 'incomplete', 'paused'
                                        )),
    current_period_start    timestamptz,
    current_period_end      timestamptz,
    cancel_at_period_end    boolean     NOT NULL DEFAULT false,
    cancelled_at            timestamptz,
    -- Timestamps
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.subscriptions IS
    'Active and historical subscription tiers per user. Synced from Polar.sh webhooks.';

CREATE TRIGGER trg_subscriptions_updated_at
    BEFORE UPDATE ON public.subscriptions
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_uuid
    ON public.subscriptions (user_uuid);

CREATE INDEX IF NOT EXISTS idx_subscriptions_status
    ON public.subscriptions (status)
    WHERE status IN ('active', 'trialing');

CREATE INDEX IF NOT EXISTS idx_subscriptions_polar_id
    ON public.subscriptions (polar_subscription_id)
    WHERE polar_subscription_id IS NOT NULL;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "subscriptions_select_own"
    ON public.subscriptions FOR SELECT TO authenticated
    USING (user_uuid = auth.uid());

CREATE POLICY "subscriptions_all_service_role"
    ON public.subscriptions FOR ALL TO service_role
    USING (true) WITH CHECK (true);
