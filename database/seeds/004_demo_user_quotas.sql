-- Seed: 004_demo_user_quotas
-- Provisions user_quotas rows for all five demo users.
-- Uses provision_user_quota() where possible, then adjusts bucket state
-- to match the desired demo scenario.
--
-- User matrix:
--   Alice  00000000-…-0001  Pro (tier 2)        workspace member, healthy
--   Bob    00000000-…-0002  Free (tier 1)        NOT in workspace, quota exhausted scenario
--   Carol  00000000-…-0003  Pro (tier 2)         workspace member, budget-exhausted scenario
--   Dave   00000000-…-0004  Enterprise (tier 3)  workspace member (admin), healthy
--   Eve    00000000-…-0005  Pro (tier 2)         suspended workspace + account suspended
--
-- Prerequisite: 002_demo_auth_users.sql

-- ── Provision base quota rows via RPC ─────────────────────────────────────────
-- provision_user_quota() is idempotent (ON CONFLICT DO NOTHING).

SELECT public.provision_user_quota('00000000-0000-0000-0000-000000000001'::uuid, 2);  -- Pro
SELECT public.provision_user_quota('00000000-0000-0000-0000-000000000002'::uuid, 1);  -- Free
SELECT public.provision_user_quota('00000000-0000-0000-0000-000000000003'::uuid, 2);  -- Pro
SELECT public.provision_user_quota('00000000-0000-0000-0000-000000000004'::uuid, 3);  -- Enterprise
SELECT public.provision_user_quota('00000000-0000-0000-0000-000000000005'::uuid, 2);  -- Pro

-- ── Scenario: Eve suspended ───────────────────────────────────────────────────
-- Eve's account is individually suspended (separate from workspace suspension).

UPDATE public.user_quotas
   SET is_suspended       = true,
       suspension_reason  = 'Policy violation: automated abuse detection triggered'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000005';

-- ── Set realistic current bucket levels ──────────────────────────────────────
-- Alice (Pro): healthy, ~60% of bucket used today
UPDATE public.user_quotas
   SET current_tokens = 40000,          -- 40k remaining (100k max for Pro)
       last_refill_at = now() - interval '2 hours'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000001';

-- Bob (Free): near-empty bucket — tight on rate-limiting too
UPDATE public.user_quotas
   SET current_tokens = 50,             -- almost empty (10k max for Free)
       last_refill_at = now() - interval '10 minutes'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000002';

-- Carol (Pro): healthy bucket but will have exhausted monthly budget via usage_counters
UPDATE public.user_quotas
   SET current_tokens = 95000,          -- bucket is fine
       last_refill_at = now() - interval '30 minutes'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000003';

-- Dave (Enterprise): healthy, large bucket
UPDATE public.user_quotas
   SET current_tokens = 800000,
       last_refill_at = now() - interval '1 hour'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000004';
