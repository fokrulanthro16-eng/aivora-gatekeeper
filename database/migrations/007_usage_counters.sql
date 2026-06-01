-- Migration: 007_usage_counters
-- Per-user monthly usage counters for message count and dollar budget.
-- A new row is created automatically for each calendar month by
-- check_and_consume_ai_usage() via INSERT … ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS public.usage_counters (
    id                  uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_uuid           uuid            NOT NULL
                                        REFERENCES auth.users (id) ON DELETE CASCADE,
    -- Each row covers exactly one calendar month (date_trunc('month', …))
    period_start        timestamptz     NOT NULL DEFAULT date_trunc('month', now()),
    -- Counters — updated atomically by check_and_consume_ai_usage()
    messages_used       integer         NOT NULL DEFAULT 0 CHECK (messages_used >= 0),
    budget_used_usd     numeric(12, 6)  NOT NULL DEFAULT 0 CHECK (budget_used_usd >= 0),
    last_updated_at     timestamptz     NOT NULL DEFAULT now(),
    created_at          timestamptz     NOT NULL DEFAULT now(),

    UNIQUE (user_uuid, period_start)
);

COMMENT ON TABLE public.usage_counters IS
    'Per-user monthly message and budget counters. One row per user per calendar month.';
COMMENT ON COLUMN public.usage_counters.messages_used IS
    'Number of OpenRouter API calls made this month.';
COMMENT ON COLUMN public.usage_counters.budget_used_usd IS
    'Cumulative estimated cost in USD charged against this month''s budget.';

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Primary lookup path: get this month's row for a given user
CREATE INDEX IF NOT EXISTS idx_usage_counters_user_period
    ON public.usage_counters (user_uuid, period_start DESC);

-- Allow efficient scans of high-usage accounts (billing reports, alerts)
CREATE INDEX IF NOT EXISTS idx_usage_counters_messages_used
    ON public.usage_counters (messages_used DESC)
    WHERE messages_used > 0;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.usage_counters ENABLE ROW LEVEL SECURITY;

CREATE POLICY "usage_counters_select_own"
    ON public.usage_counters FOR SELECT TO authenticated
    USING (user_uuid = auth.uid());

CREATE POLICY "usage_counters_all_service_role"
    ON public.usage_counters FOR ALL TO service_role
    USING (true) WITH CHECK (true);
