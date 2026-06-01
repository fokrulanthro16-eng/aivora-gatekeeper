-- Migration: 009_monthly_usage_resets
-- Immutable audit log of every monthly counter reset.
-- Written by the cron job or by check_and_consume_ai_usage when it detects
-- a new billing period, so we never lose historical consumption data.

CREATE TABLE IF NOT EXISTS public.monthly_usage_resets (
    id                          bigint          PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_uuid                   uuid            NOT NULL
                                                REFERENCES auth.users (id) ON DELETE SET NULL,
    -- Period being closed out
    period_start                timestamptz     NOT NULL,
    period_end                  timestamptz     NOT NULL,
    -- Snapshot of the counters at reset time
    final_messages_used         integer         NOT NULL DEFAULT 0,
    final_budget_used_usd       numeric(12, 6)  NOT NULL DEFAULT 0,
    -- Why this reset happened
    triggered_by                text            NOT NULL DEFAULT 'system'
                                                CHECK (triggered_by IN (
                                                    'system',           -- scheduled cron
                                                    'subscription_change', -- tier upgrade/downgrade
                                                    'manual'            -- admin action
                                                )),
    reset_at                    timestamptz     NOT NULL DEFAULT now(),
    created_at                  timestamptz     NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.monthly_usage_resets IS
    'Append-only audit log of monthly usage counter resets. Never UPDATE or DELETE.';

-- Prevent mutations
REVOKE UPDATE, DELETE, TRUNCATE ON public.monthly_usage_resets FROM PUBLIC;

CREATE INDEX IF NOT EXISTS idx_monthly_usage_resets_user_uuid
    ON public.monthly_usage_resets (user_uuid, reset_at DESC);

CREATE INDEX IF NOT EXISTS idx_monthly_usage_resets_period
    ON public.monthly_usage_resets (period_start DESC);

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.monthly_usage_resets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "monthly_usage_resets_select_own"
    ON public.monthly_usage_resets FOR SELECT TO authenticated
    USING (user_uuid = auth.uid());

CREATE POLICY "monthly_usage_resets_insert_service_role"
    ON public.monthly_usage_resets FOR INSERT TO service_role
    WITH CHECK (true);

CREATE POLICY "monthly_usage_resets_no_write_authenticated"
    ON public.monthly_usage_resets FOR ALL TO authenticated
    USING (false);
