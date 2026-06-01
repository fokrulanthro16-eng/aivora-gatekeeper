"""
Workspace management routes.

  POST   /v1/workspaces                                — create workspace
  GET    /v1/workspaces/{workspace_id}                 — get workspace
  PATCH  /v1/workspaces/{workspace_id}                 — update name / plan / budget
  GET    /v1/workspaces/{workspace_id}/members         — list members
  POST   /v1/workspaces/{workspace_id}/members         — add member
  DELETE /v1/workspaces/{workspace_id}/members/{user}  — remove member
  GET    /v1/workspaces/{workspace_id}/usage           — current-month usage
  GET    /v1/workspaces/{workspace_id}/alerts          — budget alerts
  GET    /v1/workspaces/{workspace_id}/anomalies       — spending anomalies

All routes are in GatewayMiddleware's bypass-prefix list (/v1/workspaces) and
therefore do not consume token-bucket quota.  Caller authentication relies on
Supabase RLS at the database level; the service role key used by this backend
has unrestricted access (consistent with existing route patterns).
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query

from app.models.workspace_schemas import (
    BudgetStatus,
    SpendingAnomalyRead,
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberRead,
    WorkspaceMonthlyUsageRead,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services import budget_alert as alert_svc
from app.services import workspace as ws_svc
from app.services.anomaly_detector import get_active_anomalies

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["Workspaces"])


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkspaceRead, status_code=201)
async def create_workspace(payload: WorkspaceCreate) -> WorkspaceRead:
    # Reject duplicate slugs early with a clear error
    existing = await ws_svc.get_workspace_by_slug(payload.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Slug '{payload.slug}' is already taken.")

    row = await ws_svc.create_workspace(
        name=payload.name,
        slug=payload.slug,
        owner_uuid=str(payload.owner_uuid),
        plan=payload.plan,
        monthly_budget_usd=payload.monthly_budget_usd,
    )
    if row is None:
        raise HTTPException(status_code=503, detail="Database unavailable.")
    return WorkspaceRead(**row)


# ── Read ───────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(workspace_id: str) -> WorkspaceRead:
    row = await ws_svc.get_workspace(workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return WorkspaceRead(**row)


# ── Update ─────────────────────────────────────────────────────────────────────

@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(workspace_id: str, payload: WorkspaceUpdate) -> WorkspaceRead:
    updates: dict = {
        k: v for k, v in payload.model_dump().items() if v is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    row = await ws_svc.update_workspace(workspace_id, updates)
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found or update failed.")
    return WorkspaceRead(**row)


# ── Members ────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberRead])
async def list_members(workspace_id: str) -> list[WorkspaceMemberRead]:
    rows = await ws_svc.get_members(workspace_id)
    return [WorkspaceMemberRead(**r) for r in rows]


@router.post("/{workspace_id}/members", response_model=WorkspaceMemberRead, status_code=201)
async def add_member(workspace_id: str, payload: WorkspaceMemberCreate) -> WorkspaceMemberRead:
    row = await ws_svc.add_member(
        workspace_id=workspace_id,
        user_uuid=str(payload.user_uuid),
        role=payload.role,
    )
    if row is None:
        raise HTTPException(status_code=503, detail="Could not add member.")
    return WorkspaceMemberRead(**row)


@router.delete("/{workspace_id}/members/{user_uuid}", status_code=204)
async def remove_member(workspace_id: str, user_uuid: str) -> None:
    ok = await ws_svc.remove_member(workspace_id, user_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found or removal failed.")


# ── Usage & analytics ──────────────────────────────────────────────────────────

@router.get("/{workspace_id}/usage", response_model=WorkspaceMonthlyUsageRead | None)
async def current_usage(workspace_id: str) -> WorkspaceMonthlyUsageRead | None:
    row = await ws_svc.get_current_month_usage(workspace_id)
    if row is None:
        return None
    return WorkspaceMonthlyUsageRead(**row)


@router.get("/{workspace_id}/alerts", response_model=BudgetStatus)
async def budget_alerts(workspace_id: str) -> BudgetStatus:
    status = await alert_svc.get_budget_status(workspace_id)
    return BudgetStatus(**status)


@router.get("/{workspace_id}/anomalies", response_model=list[SpendingAnomalyRead])
async def anomalies(workspace_id: str) -> list[SpendingAnomalyRead]:
    rows = await get_active_anomalies(workspace_id)
    return [SpendingAnomalyRead(**r) for r in rows]
