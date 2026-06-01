-- Migration: 004_indexes_and_rls
-- Performance indexes and Row Level Security policies for all public tables.

-- ─── INDEXES ─────────────────────────────────────────────────────────────────

-- user_quotas: primary lookup path is by user_uuid (already unique, index created by constraint)
-- Add covering index for the hot path inside process_token_bucket_leak
CREATE INDEX IF NOT EXISTS idx_user_quotas_user_uuid
    ON public.user_quotas (user_uuid)
    INCLUDE (current_tokens, last_refill_at, max_tokens, refill_rate,
             monthly_token_budget, tokens_used_this_period, is_suspended);

-- user_quotas: dashboard queries filtering by tier
CREATE INDEX IF NOT EXISTS idx_user_quotas_billing_tier_id
    ON public.user_quotas (billing_tier_id);

-- user_quotas: identify users whose monthly period needs rolling
CREATE INDEX IF NOT EXISTS idx_user_quotas_period_start
    ON public.user_quotas (period_start)
    WHERE tokens_used_this_period > 0;

-- api_logs: most queries are per-user time-range scans
CREATE INDEX IF NOT EXISTS idx_api_logs_user_uuid_created_at
    ON public.api_logs (user_uuid, created_at DESC);

-- api_logs: analytics — count denials for alerting / billing reports
CREATE INDEX IF NOT EXISTS idx_api_logs_allowed_created_at
    ON public.api_logs (allowed, created_at DESC)
    WHERE allowed = false;

-- api_logs: correlate by request_id in distributed traces
CREATE INDEX IF NOT EXISTS idx_api_logs_request_id
    ON public.api_logs (request_id)
    WHERE request_id IS NOT NULL;

-- billing_tiers: ordered list for UI display (tiny table, but keeps query plan clean)
CREATE INDEX IF NOT EXISTS idx_billing_tiers_display_order
    ON public.billing_tiers (display_order)
    WHERE is_active = true;


-- ─── ROW LEVEL SECURITY ──────────────────────────────────────────────────────

ALTER TABLE public.billing_tiers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_quotas   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_logs      ENABLE ROW LEVEL SECURITY;

-- ── billing_tiers policies ──────────────────────────────────────────────────

-- All authenticated users may read active tiers (needed for signup/upgrade UI)
CREATE POLICY "billing_tiers_select_authenticated"
    ON public.billing_tiers
    FOR SELECT
    TO authenticated
    USING (is_active = true);

-- Tier management is restricted to the service role (no direct user writes)
CREATE POLICY "billing_tiers_all_service_role"
    ON public.billing_tiers
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ── user_quotas policies ────────────────────────────────────────────────────

-- Each user reads only their own quota row
CREATE POLICY "user_quotas_select_own"
    ON public.user_quotas
    FOR SELECT
    TO authenticated
    USING (user_uuid = auth.uid());

-- Service role has full access (needed by provision_user_quota + token bucket fn)
CREATE POLICY "user_quotas_all_service_role"
    ON public.user_quotas
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ── api_logs policies ───────────────────────────────────────────────────────

-- Each user reads only their own log entries
CREATE POLICY "api_logs_select_own"
    ON public.api_logs
    FOR SELECT
    TO authenticated
    USING (user_uuid = auth.uid());

-- Only the service role (and SECURITY DEFINER functions) may insert
CREATE POLICY "api_logs_insert_service_role"
    ON public.api_logs
    FOR INSERT
    TO service_role
    WITH CHECK (true);

-- Deny all other write operations for authenticated users (belt-and-suspenders
-- alongside the REVOKE issued in migration 003)
CREATE POLICY "api_logs_no_write_authenticated"
    ON public.api_logs
    FOR ALL
    TO authenticated
    USING (false);
