"""
Workspace CRUD and usage-query service.

All public functions are async and wrap Supabase table operations via the
module-level singleton client.  Callers should treat None returns as
"not found / unavailable" rather than raising; structured errors are logged.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from app.services.supabase_client import get_supabase_client, is_supabase_available

log = structlog.get_logger(__name__)

_TIMEOUT = 5.0   # seconds for all Supabase calls


# ── Workspace CRUD ────────────────────────────────────────────────────────────

async def create_workspace(
    name: str,
    slug: str,
    owner_uuid: str,
    plan: str = "starter",
    monthly_budget_usd: float = 50.0,
) -> dict[str, Any] | None:
    """
    Create workspace + add owner member + link owner quota row atomically
    via the create_workspace() Supabase RPC.
    Returns the new workspace row or None on failure.
    """
    if not is_supabase_available():
        log.warning("create_workspace_supabase_unavailable")
        return None
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.rpc(
                "create_workspace",
                {
                    "p_name":       name,
                    "p_slug":       slug,
                    "p_owner_uuid": owner_uuid,
                    "p_plan":       plan,
                    "p_budget_usd": monthly_budget_usd,
                },
            ).execute(),
            timeout=_TIMEOUT,
        )
        log.info("workspace_created", slug=slug, owner=owner_uuid)
        return result.data  # type: ignore[return-value]
    except Exception as exc:
        log.error("create_workspace_error", slug=slug, error=str(exc))
        return None


async def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    if not is_supabase_available():
        return None
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspaces")
                .select("*")
                .eq("id", workspace_id)
                .maybe_single()
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data  # type: ignore[return-value]
    except Exception as exc:
        log.error("get_workspace_error", workspace_id=workspace_id, error=str(exc))
        return None


async def get_workspace_by_slug(slug: str) -> dict[str, Any] | None:
    if not is_supabase_available():
        return None
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspaces")
                .select("*")
                .eq("slug", slug)
                .maybe_single()
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data  # type: ignore[return-value]
    except Exception as exc:
        log.error("get_workspace_by_slug_error", slug=slug, error=str(exc))
        return None


async def list_workspaces(
    limit: int = 50,
    offset: int = 0,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        query = (
            client.table("workspaces")
                .select("*")
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
        )
        if active_only:
            query = query.eq("is_active", True).eq("is_suspended", False)
        result = await asyncio.wait_for(query.execute(), timeout=_TIMEOUT)
        return result.data or []
    except Exception as exc:
        log.error("list_workspaces_error", error=str(exc))
        return []


async def update_workspace(
    workspace_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    if not is_supabase_available():
        return None
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspaces")
                .update(updates)
                .eq("id", workspace_id)
                .execute(),
            timeout=_TIMEOUT,
        )
        data = result.data
        return data[0] if data else None
    except Exception as exc:
        log.error("update_workspace_error", workspace_id=workspace_id, error=str(exc))
        return None


async def suspend_workspace(workspace_id: str, reason: str) -> dict[str, Any] | None:
    return await update_workspace(
        workspace_id,
        {"is_suspended": True, "suspension_reason": reason},
    )


async def unsuspend_workspace(workspace_id: str) -> dict[str, Any] | None:
    return await update_workspace(
        workspace_id,
        {"is_suspended": False, "suspension_reason": None},
    )


# ── Members ───────────────────────────────────────────────────────────────────

async def get_members(workspace_id: str) -> list[dict[str, Any]]:
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_members")
                .select("*")
                .eq("workspace_id", workspace_id)
                .order("joined_at")
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data or []
    except Exception as exc:
        log.error("get_members_error", workspace_id=workspace_id, error=str(exc))
        return []


async def add_member(
    workspace_id: str,
    user_uuid: str,
    role: str = "member",
) -> dict[str, Any] | None:
    if not is_supabase_available():
        return None
    try:
        client = get_supabase_client()
        # Add to workspace_members
        result = await asyncio.wait_for(
            client.table("workspace_members")
                .upsert(
                    {"workspace_id": workspace_id, "user_uuid": user_uuid, "role": role},
                    on_conflict="workspace_id,user_uuid",
                )
                .execute(),
            timeout=_TIMEOUT,
        )
        # Link user's quota row to this workspace
        await asyncio.wait_for(
            client.table("user_quotas")
                .update({"workspace_id": workspace_id})
                .eq("user_uuid", user_uuid)
                .execute(),
            timeout=_TIMEOUT,
        )
        log.info("member_added", workspace_id=workspace_id, user_uuid=user_uuid, role=role)
        data = result.data
        return data[0] if data else None
    except Exception as exc:
        log.error("add_member_error", workspace_id=workspace_id, user_uuid=user_uuid, error=str(exc))
        return None


async def remove_member(workspace_id: str, user_uuid: str) -> bool:
    if not is_supabase_available():
        return False
    try:
        client = get_supabase_client()
        await asyncio.wait_for(
            client.table("workspace_members")
                .delete()
                .eq("workspace_id", workspace_id)
                .eq("user_uuid", user_uuid)
                .execute(),
            timeout=_TIMEOUT,
        )
        # Unlink quota row
        await asyncio.wait_for(
            client.table("user_quotas")
                .update({"workspace_id": None})
                .eq("user_uuid", user_uuid)
                .execute(),
            timeout=_TIMEOUT,
        )
        log.info("member_removed", workspace_id=workspace_id, user_uuid=user_uuid)
        return True
    except Exception as exc:
        log.error("remove_member_error", workspace_id=workspace_id, user_uuid=user_uuid, error=str(exc))
        return False


# ── Usage queries ─────────────────────────────────────────────────────────────

async def get_current_month_usage(workspace_id: str) -> dict[str, Any] | None:
    """Return workspace_monthly_usage row for the current calendar month."""
    if not is_supabase_available():
        return None
    now = datetime.now(timezone.utc)
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_monthly_usage")
                .select("*")
                .eq("workspace_id", workspace_id)
                .eq("year",  now.year)
                .eq("month", now.month)
                .maybe_single()
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data  # type: ignore[return-value]
    except Exception as exc:
        log.error("get_current_usage_error", workspace_id=workspace_id, error=str(exc))
        return None


async def get_usage_trend(
    workspace_id: str,
    months: int = 6,
) -> list[dict[str, Any]]:
    """Return up to `months` months of usage history via Supabase RPC."""
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.rpc(
                "get_workspace_usage_trend",
                {"p_workspace_id": workspace_id, "p_months": months},
            ).execute(),
            timeout=_TIMEOUT,
        )
        return result.data or []
    except Exception as exc:
        log.error("get_usage_trend_error", workspace_id=workspace_id, error=str(exc))
        return []


async def get_workspace_count() -> int:
    """Return total workspace count (admin use)."""
    if not is_supabase_available():
        return 0
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspaces").select("id", count="exact").execute(),
            timeout=_TIMEOUT,
        )
        return result.count or 0
    except Exception:
        return 0
