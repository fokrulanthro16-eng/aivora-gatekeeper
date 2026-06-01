-- Migration: 010_polar_webhook_events
-- Idempotent event store for Polar.sh webhook deliveries.
-- Every incoming webhook is persisted here before processing so that:
--   • Retries are idempotent (event_id UNIQUE)
--   • Failed processing can be replayed
--   • Subscription history is fully auditable

CREATE TABLE IF NOT EXISTS public.polar_webhook_events (
    id              bigint      PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    -- Polar's unique event ID (from the webhook-id header) — idempotency key
    event_id        text        NOT NULL UNIQUE,
    event_type      text        NOT NULL,    -- e.g. "subscription.created"
    -- Raw payload stored for replay / debugging
    payload         jsonb       NOT NULL,
    -- Resolved user after processing (NULL if we couldn't match the customer)
    user_uuid       uuid        REFERENCES auth.users (id) ON DELETE SET NULL,
    -- Processing state
    processed       boolean     NOT NULL DEFAULT false,
    processed_at    timestamptz,
    -- If processing failed, the error message is stored here
    error           text,
    retry_count     smallint    NOT NULL DEFAULT 0,
    -- Timestamps
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.polar_webhook_events IS
    'Idempotent store for Polar.sh webhook events. Processed asynchronously.';

-- Prevent tampering with the event record
REVOKE UPDATE (event_id, event_type, payload, created_at) ON public.polar_webhook_events FROM PUBLIC;
REVOKE DELETE, TRUNCATE ON public.polar_webhook_events FROM PUBLIC;

CREATE INDEX IF NOT EXISTS idx_polar_webhook_events_unprocessed
    ON public.polar_webhook_events (created_at)
    WHERE processed = false;

CREATE INDEX IF NOT EXISTS idx_polar_webhook_events_user_uuid
    ON public.polar_webhook_events (user_uuid)
    WHERE user_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_polar_webhook_events_type
    ON public.polar_webhook_events (event_type, created_at DESC);

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.polar_webhook_events ENABLE ROW LEVEL SECURITY;

-- Only service role reads/writes webhook events; authenticated users see nothing
CREATE POLICY "polar_webhook_events_all_service_role"
    ON public.polar_webhook_events FOR ALL TO service_role
    USING (true) WITH CHECK (true);

CREATE POLICY "polar_webhook_events_deny_authenticated"
    ON public.polar_webhook_events FOR ALL TO authenticated
    USING (false);
