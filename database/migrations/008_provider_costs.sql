-- Migration: 008_provider_costs
-- Reference table of per-model token costs for supported OpenRouter providers.
-- Used by the backend cost estimator before calling the OpenRouter API.
-- Prices are USD per 1,000 tokens and can be updated without a code deploy.

CREATE TABLE IF NOT EXISTS public.provider_costs (
    id                          bigint          PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    provider                    text            NOT NULL,
    model                       text            NOT NULL,
    -- Cost in USD per 1 000 tokens
    input_cost_per_1k_usd       numeric(12, 8)  NOT NULL DEFAULT 0 CHECK (input_cost_per_1k_usd >= 0),
    output_cost_per_1k_usd      numeric(12, 8)  NOT NULL DEFAULT 0 CHECK (output_cost_per_1k_usd >= 0),
    -- Context window (tokens)
    context_window              integer,
    max_output_tokens           integer,
    is_active                   boolean         NOT NULL DEFAULT true,
    notes                       text,
    created_at                  timestamptz     NOT NULL DEFAULT now(),
    updated_at                  timestamptz     NOT NULL DEFAULT now(),

    UNIQUE (provider, model)
);

COMMENT ON TABLE public.provider_costs IS
    'OpenRouter model pricing reference. Prices in USD per 1 000 tokens. '
    'Updated from https://openrouter.ai/docs#models';

CREATE TRIGGER trg_provider_costs_updated_at
    BEFORE UPDATE ON public.provider_costs
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE INDEX IF NOT EXISTS idx_provider_costs_provider_model
    ON public.provider_costs (provider, model)
    WHERE is_active = true;

-- ── Seed: OpenRouter model pricing (as of 2025) ───────────────────────────────

INSERT INTO public.provider_costs
    (provider,      model,                              input_cost_per_1k_usd, output_cost_per_1k_usd, context_window, max_output_tokens, notes)
VALUES
    -- OpenAI
    ('openai',      'gpt-4o',                           0.00500,  0.01500,  128000, 16384,  'GPT-4o flagship'),
    ('openai',      'gpt-4o-mini',                      0.00015,  0.00060,  128000, 16384,  'GPT-4o Mini — cheapest GPT-4 class'),
    ('openai',      'gpt-4-turbo',                      0.01000,  0.03000,  128000, 4096,   'GPT-4 Turbo'),
    ('openai',      'o1',                               0.01500,  0.06000,  200000, 100000, 'o1 reasoning model'),
    ('openai',      'o1-mini',                          0.00300,  0.01200,  128000, 65536,  'o1-mini reasoning'),
    -- Anthropic
    ('anthropic',   'claude-3-5-sonnet',                0.00300,  0.01500,  200000, 8192,   'Claude 3.5 Sonnet — best value'),
    ('anthropic',   'claude-3-5-haiku',                 0.00080,  0.00400,  200000, 8192,   'Claude 3.5 Haiku — fast'),
    ('anthropic',   'claude-3-opus',                    0.01500,  0.07500,  200000, 4096,   'Claude 3 Opus — most capable'),
    -- Google
    ('google',      'gemini-pro-1.5',                   0.00125,  0.00500,  2000000, 8192,  'Gemini 1.5 Pro — huge context'),
    ('google',      'gemini-flash-1.5',                 0.000075, 0.00030, 1000000, 8192,   'Gemini 1.5 Flash — fastest'),
    ('google',      'gemini-flash-2.0',                 0.000075, 0.00030,  1000000, 8192,  'Gemini 2.0 Flash'),
    -- Meta
    ('meta-llama',  'llama-3.1-8b-instruct',            0.000055, 0.000055, 131072, 8192,   'Llama 3.1 8B — open source'),
    ('meta-llama',  'llama-3.1-70b-instruct',           0.000520, 0.000750, 131072, 8192,   'Llama 3.1 70B'),
    ('meta-llama',  'llama-3.1-405b-instruct',          0.002700, 0.002700, 131072, 8192,   'Llama 3.1 405B'),
    -- Mistral
    ('mistralai',   'mistral-7b-instruct',              0.000055, 0.000055, 32768,  8192,   'Mistral 7B'),
    ('mistralai',   'mixtral-8x7b-instruct',            0.000240, 0.000240, 32768,  8192,   'Mixtral 8x7B MoE'),
    ('mistralai',   'mistral-large',                    0.002000, 0.006000, 128000, 8192,   'Mistral Large')
ON CONFLICT (provider, model) DO NOTHING;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.provider_costs ENABLE ROW LEVEL SECURITY;

-- All authenticated users may read pricing (needed for cost estimates in UI)
CREATE POLICY "provider_costs_select_authenticated"
    ON public.provider_costs FOR SELECT TO authenticated
    USING (is_active = true);

CREATE POLICY "provider_costs_all_service_role"
    ON public.provider_costs FOR ALL TO service_role
    USING (true) WITH CHECK (true);
