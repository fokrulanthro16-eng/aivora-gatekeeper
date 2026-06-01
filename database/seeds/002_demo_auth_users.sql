-- Seed: 002_demo_auth_users
-- Insert five demo users directly into auth.users.
-- REQUIRES: service_role credentials; run in Supabase SQL Editor or via psql
--           with the direct database URL (not the PostgREST URL).
--           Alternatively run scripts/seed_demo.py which uses the Admin API
--           and does not require direct DB access.
--
-- Demo UUIDs (fixed so every seed script can reference them by constant):
--   00000000-0000-0000-0000-000000000001  alice@demo.aivora.ai  Pro  (frontend demo user)
--   00000000-0000-0000-0000-000000000002  bob@demo.aivora.ai    Free (quota exhausted)
--   00000000-0000-0000-0000-000000000003  carol@demo.aivora.ai  Pro  (budget exhausted)
--   00000000-0000-0000-0000-000000000004  dave@demo.aivora.ai   Enterprise (healthy)
--   00000000-0000-0000-0000-000000000005  eve@demo.aivora.ai    Pro  (account suspended)
--
-- All demo passwords: Demo1234!
-- Safe to re-run: ON CONFLICT (id) DO NOTHING

DO $$
BEGIN
    -- pgcrypto must be enabled for crypt() / gen_salt()
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        CREATE EXTENSION pgcrypto;
    END IF;
END $$;

INSERT INTO auth.users (
    id,
    instance_id,
    aud,
    role,
    email,
    encrypted_password,
    email_confirmed_at,
    raw_app_meta_data,
    raw_user_meta_data,
    created_at,
    updated_at
)
VALUES
    -- 001 Alice — Pro tier, workspace member, used by the frontend demo button
    (
        '00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'alice@demo.aivora.ai',
        crypt('Demo1234!', gen_salt('bf')),
        now(),
        '{"provider":"email","providers":["email"]}',
        '{"name":"Alice Demo","role":"pro"}',
        now(), now()
    ),
    -- 002 Bob — Free tier; monthly message limit will be exhausted in scenario seed
    (
        '00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'bob@demo.aivora.ai',
        crypt('Demo1234!', gen_salt('bf')),
        now(),
        '{"provider":"email","providers":["email"]}',
        '{"name":"Bob Demo","role":"free"}',
        now(), now()
    ),
    -- 003 Carol — Pro tier; monthly dollar budget will be near-exhausted in scenario seed
    (
        '00000000-0000-0000-0000-000000000003',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'carol@demo.aivora.ai',
        crypt('Demo1234!', gen_salt('bf')),
        now(),
        '{"provider":"email","providers":["email"]}',
        '{"name":"Carol Demo","role":"pro"}',
        now(), now()
    ),
    -- 004 Dave — Enterprise tier; healthy with normal usage
    (
        '00000000-0000-0000-0000-000000000004',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'dave@demo.aivora.ai',
        crypt('Demo1234!', gen_salt('bf')),
        now(),
        '{"provider":"email","providers":["email"]}',
        '{"name":"Dave Demo","role":"enterprise"}',
        now(), now()
    ),
    -- 005 Eve — Pro tier; account suspended
    (
        '00000000-0000-0000-0000-000000000005',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'eve@demo.aivora.ai',
        crypt('Demo1234!', gen_salt('bf')),
        now(),
        '{"provider":"email","providers":["email"]}',
        '{"name":"Eve Demo","role":"suspended"}',
        now(), now()
    )
ON CONFLICT (id) DO NOTHING;
