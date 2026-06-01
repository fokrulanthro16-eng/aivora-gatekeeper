-- Seed: 003_demo_workspace
-- Creates the demo workspace "Acme AI Co" and a suspended workspace
-- "Frozen Corp" for scenario testing.
-- Adds Alice (Pro), Dave (Enterprise), and Carol (Pro) as members of Acme AI Co.
-- Links their user_quotas.workspace_id to the workspace.
--
-- Workspace UUIDs:
--   aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa  Acme AI Co  (growth, $100/mo budget)
--   bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb  Frozen Corp (starter, $50/mo, SUSPENDED)
--
-- Prerequisite: 002_demo_auth_users.sql and 004_demo_user_quotas.sql must run first.
-- Safe to re-run: ON CONFLICT … DO UPDATE / DO NOTHING throughout.

-- ── Workspaces ────────────────────────────────────────────────────────────────

INSERT INTO public.workspaces
    (id, name, slug, owner_uuid, plan, monthly_budget_usd,
     is_active, is_suspended, suspension_reason)
VALUES
    (
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        'Acme AI Co',
        'acme-ai-co',
        '00000000-0000-0000-0000-000000000001',  -- Alice is owner
        'growth',
        100.00,
        true, false, NULL
    ),
    (
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        'Frozen Corp',
        'frozen-corp',
        '00000000-0000-0000-0000-000000000005',  -- Eve is owner
        'starter',
        50.00,
        true, true,
        'Fraudulent activity detected — account under review. Ref: FRAUD-2026-0601'
    )
ON CONFLICT (id) DO UPDATE
    SET name               = EXCLUDED.name,
        plan               = EXCLUDED.plan,
        monthly_budget_usd = EXCLUDED.monthly_budget_usd,
        is_suspended       = EXCLUDED.is_suspended,
        suspension_reason  = EXCLUDED.suspension_reason,
        updated_at         = now();

-- ── Members ───────────────────────────────────────────────────────────────────
-- Acme AI Co: Alice (owner), Dave (admin), Carol (member)
-- Frozen Corp: Eve (owner)

INSERT INTO public.workspace_members (workspace_id, user_uuid, role)
VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '00000000-0000-0000-0000-000000000001', 'owner'),
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '00000000-0000-0000-0000-000000000004', 'admin'),
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '00000000-0000-0000-0000-000000000003', 'member'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '00000000-0000-0000-0000-000000000005', 'owner')
ON CONFLICT (workspace_id, user_uuid) DO UPDATE
    SET role = EXCLUDED.role;

-- ── Link workspace_id onto user_quotas ───────────────────────────────────────
UPDATE public.user_quotas
   SET workspace_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
 WHERE user_uuid IN (
     '00000000-0000-0000-0000-000000000001',   -- Alice
     '00000000-0000-0000-0000-000000000004',   -- Dave
     '00000000-0000-0000-0000-000000000003'    -- Carol
 );

UPDATE public.user_quotas
   SET workspace_id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
 WHERE user_uuid = '00000000-0000-0000-0000-000000000005';  -- Eve
