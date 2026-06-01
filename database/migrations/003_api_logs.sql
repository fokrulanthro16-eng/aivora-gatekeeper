-- Migration: 003_api_logs
-- Immutable append-only audit trail written by process_token_bucket_leak().
-- Never UPDATE or DELETE rows; use range-delete partitioning for retention.

CREATE TABLE IF NOT EXISTS public.api_logs (
    id              bigint          PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    user_uuid       uuid            NOT NULL
                                    REFERENCES auth.users (id) ON DELETE SET NULL,
    -- Request context
    endpoint        text,
    http_method     text,
    request_id      text,           -- caller-supplied correlation id (X-Request-ID)
    -- Token accounting snapshot
    request_cost    integer         NOT NULL CHECK (request_cost > 0),
    tokens_before   numeric(18, 4)  NOT NULL,
    tokens_after    numeric(18, 4)  NOT NULL,
    -- Decision
    allowed         boolean         NOT NULL,
    reason          text            NOT NULL,   -- 'allowed' | 'insufficient_tokens' | 'monthly_budget_exceeded' | 'suspended' | 'quota_not_found'
    -- Latency instrumentation (milliseconds)
    processing_ms   integer,
    -- Immutable timestamp — no updated_at on an audit table
    created_at      timestamptz     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public.api_logs IS 'Immutable audit log written by process_token_bucket_leak(). Do not UPDATE or DELETE.';
COMMENT ON COLUMN public.api_logs.reason       IS 'Machine-readable outcome code from the token bucket function.';
COMMENT ON COLUMN public.api_logs.request_id   IS 'Optional caller-supplied correlation ID for distributed tracing.';
COMMENT ON COLUMN public.api_logs.tokens_before IS 'Bucket level after continuous refill, before deduction.';
COMMENT ON COLUMN public.api_logs.tokens_after  IS 'Bucket level after deduction (equals tokens_before when request is rejected).';

-- Prevent application-layer mutations; only the token bucket function may insert.
-- Revoke is additive — the function runs SECURITY DEFINER so it bypasses this.
REVOKE UPDATE, DELETE, TRUNCATE ON public.api_logs FROM PUBLIC;
