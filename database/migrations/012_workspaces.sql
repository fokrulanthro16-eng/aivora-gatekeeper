-- Migration: 012_workspaces
-- Workspace entity — the top-level billing unit for a SaaS AI aggregator tenant.
-- Each workspace has a monthly USD budget cap that applies across all its users.
--
-- Tables created:
--   public.workspaces       — workspace entity
--   public.workspace_members — user↔workspace membership
--
-- Column added:
--   public.user_quotas.workspace_id  (FK → workspaces, nullable)

-- ── Workspaces ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.workspaces (
    id                  uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text            NOT NULL CHECK (length(trim(name)) >= 2),
    -- URL-safe identifier, unique across the platform
    slug                text            NOT NULL UNIQUE
                                        CHECK (slug ~ '^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$'),
    owner_uuid          uuid            NOT NULL,
    -- Workspace plan tier
    plan                text            NOT NULL DEFAULT 'starter'
                                        CHECK (plan IN ('starter', 'growth', 'enterprise')),
    -- Hard monthly USD spending cap across all workspace members combined
    monthly_budget_usd  numeric(12, 4)  NOT NULL DEFAULT 50.00
                                        CHECK (monthly_budget_usd > 0),
    -- Administrative flags
    is_active           boolean         NOT NULL DEFAULT true,
    is_suspended        boolean         NOT NULL DEFAULT false,
    suspension_reason   text,
    -- Timestamps
    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public.workspaces IS
    'Top-level billing unit for multi-tenant SaaS aggregator. '
    'Every workspace has a monthly_budget_usd hard cap shared by all its members.';
COMMENT ON COLUMN public.workspaces.plan IS
    'starter: $50/mo default; growth: $500/mo default; enterprise: custom budget.';
COMMENT ON COLUMN public.workspaces.monthly_budget_usd IS
    'Hard monthly spending cap in USD. Enforced by workspace_check_and_consume_usage().';

CREATE TRIGGER trg_workspaces_updated_at
    BEFORE UPDATE ON public.workspaces
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE INDEX IF NOT EXISTS idx_workspaces_owner
    ON public.workspaces (owner_uuid);

CREATE INDEX IF NOT EXISTS idx_workspaces_active
    ON public.workspaces (is_active, is_suspended)
    WHERE is_active = true AND is_suspended = false;

-- ── Workspace membership ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.workspace_members (
    workspace_id    uuid        NOT NULL REFERENCES public.workspaces (id) ON DELETE CASCADE,
    user_uuid       uuid        NOT NULL,
    role            text        NOT NULL DEFAULT 'member'
                                CHECK (role IN ('owner', 'admin', 'member')),
    joined_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_uuid)
);

COMMENT ON TABLE  public.workspace_members IS
    'Workspace membership. A user may be a member of one workspace at a time.';
COMMENT ON COLUMN public.workspace_members.role IS
    'owner: full control + billing; admin: member management; member: usage only.';

CREATE INDEX IF NOT EXISTS idx_workspace_members_user
    ON public.workspace_members (user_uuid);

-- ── Extend user_quotas with workspace linkage ─────────────────────────────────
-- NULL = personal user, not assigned to any workspace.
-- When non-NULL, the workspace_check_and_consume_usage RPC enforces the workspace
-- budget cap BEFORE the per-user quota checks.

ALTER TABLE public.user_quotas
    ADD COLUMN IF NOT EXISTS workspace_id uuid
        REFERENCES public.workspaces (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_user_quotas_workspace
    ON public.user_quotas (workspace_id)
    WHERE workspace_id IS NOT NULL;

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workspaces_select_member"
    ON public.workspaces FOR SELECT TO authenticated
    USING (
        id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_uuid = auth.uid()
        )
    );

CREATE POLICY "workspaces_all_service_role"
    ON public.workspaces FOR ALL TO service_role
    USING (true) WITH CHECK (true);

ALTER TABLE public.workspace_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workspace_members_select_own"
    ON public.workspace_members FOR SELECT TO authenticated
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members wm2
            WHERE wm2.user_uuid = auth.uid()
        )
    );

CREATE POLICY "workspace_members_all_service_role"
    ON public.workspace_members FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- ── Helper: provision workspace + add owner as first member ──────────────────

CREATE OR REPLACE FUNCTION public.create_workspace(
    p_name          text,
    p_slug          text,
    p_owner_uuid    uuid,
    p_plan          text    DEFAULT 'starter',
    p_budget_usd    numeric DEFAULT 50.00
)
RETURNS public.workspaces
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace public.workspaces%ROWTYPE;
BEGIN
    INSERT INTO public.workspaces (name, slug, owner_uuid, plan, monthly_budget_usd)
    VALUES (p_name, p_slug, p_owner_uuid, p_plan, p_budget_usd)
    RETURNING * INTO v_workspace;

    INSERT INTO public.workspace_members (workspace_id, user_uuid, role)
    VALUES (v_workspace.id, p_owner_uuid, 'owner');

    -- Link owner's quota row to the new workspace
    UPDATE public.user_quotas
    SET workspace_id = v_workspace.id
    WHERE user_uuid = p_owner_uuid;

    RETURN v_workspace;
END;
$$;

COMMENT ON FUNCTION public.create_workspace IS
    'Atomic workspace creation: inserts workspace, adds owner as member, '
    'and links owner quota row. Returns the new workspace row.';

GRANT EXECUTE ON FUNCTION public.create_workspace(text, text, uuid, text, numeric)
    TO service_role;
