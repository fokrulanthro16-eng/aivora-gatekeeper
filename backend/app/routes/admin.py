"""
Admin dashboard API.

  GET    /v1/admin/stats                             — platform-wide counters
  GET    /v1/admin/workspaces                        — all workspaces with usage
  GET    /v1/admin/workspaces/{workspace_id}         — single workspace detail
  PATCH  /v1/admin/workspaces/{workspace_id}/suspend   — suspend workspace
  PATCH  /v1/admin/workspaces/{workspace_id}/unsuspend — unsuspend workspace
  GET    /v1/admin/anomalies                         — global unresolved anomalies
  PATCH  /v1/admin/anomalies/{anomaly_id}/resolve    — mark anomaly resolved

All routes require the X-Admin-Key header matching ADMIN_API_KEY in settings.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader

from app.core.config import get_settings
from app.models.workspace_schemas import (
    PlatformStats,
    SpendingAnomalyRead,
    SuspendRequest,
    WorkspaceWithStats,
)
from app.services import workspace as ws_svc
from app.services.anomaly_detector import get_all_active_anomalies, resolve_anomaly
from app.services.supabase_client import get_supabase_client, is_supabase_available

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["Admin"])

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin(key: str | None = Depends(_admin_key_header)) -> None:
    expected = get_settings().ADMIN_API_KEY
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled. Set ADMIN_API_KEY in your environment.",
        )
    if not key or key != expected:
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing X-Admin-Key header.",
        )


# ── Platform stats ─────────────────────────────────────────────────────────────

@router.get("/stats", response_model=PlatformStats, dependencies=[Depends(_require_admin)])
async def platform_stats(
    year:  int | None = Query(default=None, ge=2024, le=2099),
    month: int | None = Query(default=None, ge=1,    le=12),
) -> PlatformStats:
    if not is_supabase_available():
        raise HTTPException(status_code=503, detail="Database unavailable.")

    now = datetime.now(timezone.utc)
    client = get_supabase_client()

    result = await asyncio.wait_for(
        client.rpc(
            "get_platform_stats",
            {
                "p_year":  year  or now.year,
                "p_month": month or now.month,
            },
        ).execute(),
        timeout=5.0,
    )
    data: dict[str, Any] = result.data or {}
    return PlatformStats(
        period=data.get("period", ""),
        total_workspaces=int(data.get("total_workspaces", 0)),
        active_workspaces=int(data.get("active_workspaces", 0)),
        suspended_workspaces=int(data.get("suspended_workspaces", 0)),
        total_cost_usd=float(data.get("total_cost_usd", 0)),
        total_requests=int(data.get("total_requests", 0)),
        blocked_requests=int(data.get("blocked_requests", 0)),
        workspaces_over_80pct=int(data.get("workspaces_over_80pct", 0)),
        active_anomalies=int(data.get("active_anomalies", 0)),
    )


# ── Workspace listing ──────────────────────────────────────────────────────────

@router.get(
    "/workspaces",
    response_model=list[WorkspaceWithStats],
    dependencies=[Depends(_require_admin)],
)
async def list_workspaces(
    limit:       int  = Query(default=50, ge=1, le=200),
    offset:      int  = Query(default=0,  ge=0),
    active_only: bool = Query(default=False),
) -> list[WorkspaceWithStats]:
    workspaces = await ws_svc.list_workspaces(limit=limit, offset=offset, active_only=active_only)
    if not workspaces:
        return []

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    # Enrich with current-month usage stats concurrently
    async def _enrich(ws: dict[str, Any]) -> WorkspaceWithStats:
        ws_id = ws["id"]
        usage, anomaly_count, member_count = await asyncio.gather(
            _safe_usage(ws_id, year, month),
            _count_anomalies(ws_id),
            _count_members(ws_id),
        )
        budget = float(ws.get("monthly_budget_usd", 0) or 0)
        spend = float(usage.get("total_cost_usd", 0) or 0) if usage else 0.0
        utilisation = round((spend / budget) * 100, 2) if budget > 0 else 0.0
        return WorkspaceWithStats(
            **ws,
            current_month_spend_usd=spend,
            current_month_requests=int((usage or {}).get("total_requests", 0)),
            budget_utilisation_pct=utilisation,
            active_anomalies=anomaly_count,
            member_count=member_count,
        )

    results = await asyncio.gather(*[_enrich(ws) for ws in workspaces])
    return list(results)


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceWithStats,
    dependencies=[Depends(_require_admin)],
)
async def get_workspace(workspace_id: str) -> WorkspaceWithStats:
    ws = await ws_svc.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    usage, anomaly_count, member_count = await asyncio.gather(
        _safe_usage(workspace_id, year, month),
        _count_anomalies(workspace_id),
        _count_members(workspace_id),
    )
    budget = float(ws.get("monthly_budget_usd", 0) or 0)
    spend = float((usage or {}).get("total_cost_usd", 0) or 0)
    utilisation = round((spend / budget) * 100, 2) if budget > 0 else 0.0

    return WorkspaceWithStats(
        **ws,
        current_month_spend_usd=spend,
        current_month_requests=int((usage or {}).get("total_requests", 0)),
        budget_utilisation_pct=utilisation,
        active_anomalies=anomaly_count,
        member_count=member_count,
    )


# ── Suspend / unsuspend ────────────────────────────────────────────────────────

@router.patch(
    "/workspaces/{workspace_id}/suspend",
    response_model=dict,
    dependencies=[Depends(_require_admin)],
)
async def suspend_workspace(workspace_id: str, payload: SuspendRequest) -> dict:
    row = await ws_svc.suspend_workspace(workspace_id, payload.reason)
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    log.info("workspace_suspended_via_admin", workspace_id=workspace_id, reason=payload.reason)
    return {"workspace_id": workspace_id, "suspended": True, "reason": payload.reason}


@router.patch(
    "/workspaces/{workspace_id}/unsuspend",
    response_model=dict,
    dependencies=[Depends(_require_admin)],
)
async def unsuspend_workspace(workspace_id: str) -> dict:
    row = await ws_svc.unsuspend_workspace(workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    log.info("workspace_unsuspended_via_admin", workspace_id=workspace_id)
    return {"workspace_id": workspace_id, "suspended": False}


# ── Anomaly feed ───────────────────────────────────────────────────────────────

@router.get(
    "/anomalies",
    response_model=list[SpendingAnomalyRead],
    dependencies=[Depends(_require_admin)],
)
async def global_anomalies(limit: int = Query(default=50, ge=1, le=200)) -> list[SpendingAnomalyRead]:
    rows = await get_all_active_anomalies(limit=limit)
    return [SpendingAnomalyRead(**r) for r in rows]


@router.patch(
    "/anomalies/{anomaly_id}/resolve",
    response_model=dict,
    dependencies=[Depends(_require_admin)],
)
async def resolve(anomaly_id: str) -> dict:
    ok = await resolve_anomaly(anomaly_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Anomaly not found or already resolved.")
    return {"anomaly_id": anomaly_id, "resolved": True}


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _safe_usage(workspace_id: str, year: int, month: int) -> dict[str, Any] | None:
    if not is_supabase_available():
        return None
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_monthly_usage")
                .select("total_cost_usd, total_requests")
                .eq("workspace_id", workspace_id)
                .eq("year",  year)
                .eq("month", month)
                .maybe_single()
                .execute(),
            timeout=3.0,
        )
        return result.data  # type: ignore[return-value]
    except Exception:
        return None


async def _count_anomalies(workspace_id: str) -> int:
    if not is_supabase_available():
        return 0
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("spending_anomalies")
                .select("id", count="exact")
                .eq("workspace_id", workspace_id)
                .eq("resolved", False)
                .execute(),
            timeout=3.0,
        )
        return result.count or 0
    except Exception:
        return 0


async def _count_members(workspace_id: str) -> int:
    if not is_supabase_available():
        return 0
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_members")
                .select("user_uuid", count="exact")
                .eq("workspace_id", workspace_id)
                .execute(),
            timeout=3.0,
        )
        return result.count or 0
    except Exception:
        return 0
