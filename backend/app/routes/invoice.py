"""
Invoice and usage trend routes.

  GET /v1/invoices/{workspace_id}/{year}/{month}  — monthly invoice summary
  GET /v1/invoices/{workspace_id}/trend           — rolling N-month usage trend

These routes are read-only analytics surfaces built on the
workspace_monthly_usage aggregate table and usage_counters per-member rows.
They are suitable for billing pages, PDF invoice generation, and BI tools.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from app.models.workspace_schemas import InvoiceSummary, UsageTrendPoint
from app.services.supabase_client import get_supabase_client, is_supabase_available
from app.services.workspace import get_usage_trend

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/invoices", tags=["Invoices"])


@router.get(
    "/{workspace_id}/{year}/{month}",
    response_model=InvoiceSummary,
    summary="Full invoice summary for a workspace billing month.",
)
async def invoice_summary(
    workspace_id: str,
    year:  int,
    month: int,
) -> InvoiceSummary:
    if not (2024 <= year <= 2099):
        raise HTTPException(status_code=422, detail="year must be between 2024 and 2099.")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=422, detail="month must be between 1 and 12.")

    if not is_supabase_available():
        raise HTTPException(status_code=503, detail="Database unavailable.")

    client = get_supabase_client()
    try:
        result = await asyncio.wait_for(
            client.rpc(
                "get_workspace_invoice_summary",
                {
                    "p_workspace_id": workspace_id,
                    "p_year":         year,
                    "p_month":        month,
                },
            ).execute(),
            timeout=10.0,
        )
    except Exception as exc:
        log.error("invoice_rpc_error", workspace_id=workspace_id, error=str(exc))
        raise HTTPException(status_code=503, detail="Invoice query failed.") from exc

    data: dict[str, Any] = result.data or {}

    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    # Normalise member_breakdown
    members = [
        {"user_uuid": m["user_uuid"], "messages_used": int(m["messages_used"] or 0),
         "budget_used_usd": float(m["budget_used_usd"] or 0)}
        for m in (data.get("member_breakdown") or [])
    ]

    # Normalise budget_alerts
    alerts = [
        {
            "threshold_pct":    int(a["threshold_pct"]),
            "triggered_at":     a["triggered_at"],
            "spend_at_trigger": float(a["spend_at_trigger"]),
            "budget_usd":       float(data.get("budget_usd") or 0),
        }
        for a in (data.get("budget_alerts") or [])
    ]

    return InvoiceSummary(
        workspace_id=workspace_id,
        workspace_name=data.get("workspace_name", ""),
        workspace_slug=data.get("workspace_slug", ""),
        workspace_plan=data.get("workspace_plan", ""),
        period=data.get("period", f"{year:04d}-{month:02d}"),
        budget_usd=float(data.get("budget_usd") or 0),
        total_cost_usd=float(data.get("total_cost_usd") or 0),
        total_requests=int(data.get("total_requests") or 0),
        blocked_requests=int(data.get("blocked_requests") or 0),
        total_tokens=int(data.get("total_tokens") or 0),
        last_request_at=data.get("last_request_at"),
        budget_utilisation_pct=float(data.get("budget_utilisation_pct") or 0),
        member_breakdown=members,
        budget_alerts=alerts,
    )


@router.get(
    "/{workspace_id}/trend",
    response_model=list[UsageTrendPoint],
    summary="Rolling N-month usage trend for a workspace.",
)
async def usage_trend(
    workspace_id: str,
    months: int = Query(default=6, ge=1, le=24),
) -> list[UsageTrendPoint]:
    rows = await get_usage_trend(workspace_id, months=months)
    return [
        UsageTrendPoint(
            year=r["year"],
            month=r["month"],
            period=r["period"],
            total_cost_usd=float(r.get("total_cost_usd") or 0),
            total_requests=int(r.get("total_requests") or 0),
            blocked_requests=int(r.get("blocked_requests") or 0),
            total_tokens=int(r.get("total_tokens") or 0),
        )
        for r in rows
    ]
