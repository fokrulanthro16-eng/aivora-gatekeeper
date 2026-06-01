-- Seed: 001_billing_tiers
-- Idempotent seed for the three standard tiers.
-- Safe to re-run: ON CONFLICT (name) DO UPDATE keeps values in sync.
--
-- Token bucket parameters:
--   Free       — 10 k  max burst, 1 token/sec  refill, 500 k  monthly hard cap
--   Pro        — 100 k max burst, 10 tokens/sec refill, 10 M  monthly hard cap
--   Enterprise — 1 M  max burst, 100 tokens/sec refill, 500 M monthly hard cap

INSERT INTO public.billing_tiers
    (name,         max_tokens,  refill_rate, monthly_token_budget, price_usd_cents, display_order)
VALUES
    ('Free',        10000,        1.0,          500000,              0,      1),
    ('Pro',        100000,       10.0,        10000000,           2900,      2),
    ('Enterprise', 1000000,     100.0,       500000000,          29900,      3)
ON CONFLICT (name) DO UPDATE
    SET max_tokens           = EXCLUDED.max_tokens,
        refill_rate          = EXCLUDED.refill_rate,
        monthly_token_budget = EXCLUDED.monthly_token_budget,
        price_usd_cents      = EXCLUDED.price_usd_cents,
        display_order        = EXCLUDED.display_order,
        updated_at           = now();
