"""
Budget alert reader service.

Reads workspace_budget_alerts and workspace_monthly_usage to produce a
BudgetStatus snapshot.  Threshold alert *insertion* is handled inside the
workspace_check_and_consume_usage() SQL RPC — this service is read-only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from app.services.supabase_client import get_supabase_client, is_supabase_available

log = structlog.get_logger(__name__)

_TIMEOUT = 5.0


async def get_alerts_for_month(
    workspace_id: str,
    year: int,
    month: int,
) -> list[dict[str, Any]]:
    """Return budget alerts fired for this workspace in the given month, ordered by threshold."""
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_budget_alerts")
                .select("threshold_pct, triggered_at, spend_at_trigger, budget_usd")
                .eq("workspace_id", workspace_id)
                .eq("year",  year)
                .eq("month", month)
                .order("threshold_pct")
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data or []
    except Exception as exc:
        log.error("get_alerts_error", workspace_id=workspace_id, error=str(exc))
        return []


async def get_budget_status(workspace_id: str) -> dict[str, Any]:
    """
    Return current budget status for a workspace.

    Fetches workspace budget, current-month spend, and any triggered alerts
    concurrently and assembles a BudgetStatus-compatible dict.
    """
    if not is_supabase_available():
        return _zero_status(workspace_id)

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    client = get_supabase_client()

    async def _get_workspace() -> dict[str, Any] | None:
        try:
            r = await asyncio.wait_for(
                client.table("workspaces")
                    .select("monthly_budget_usd")
                    .eq("id", workspace_id)
                    .maybe_single()
                    .execute(),
                timeout=_TIMEOUT,
            )
            return r.data  # type: ignore[return-value]
        except Exception:
            return None

    async def _get_spend() -> float:
        try:
            r = await asyncio.wait_for(
                client.table("workspace_monthly_usage")
                    .select("total_cost_usd")
                    .eq("workspace_id", workspace_id)
                    .eq("year",  year)
                    .eq("month", month)
                    .maybe_single()
                    .execute(),
                timeout=_TIMEOUT,
            )
            return float(r.data["total_cost_usd"]) if r.data else 0.0
        except Exception:
            return 0.0

    workspace_row, spend, alerts = await asyncio.gather(
        _get_workspace(),
        _get_spend(),
        get_alerts_for_month(workspace_id, year, month),
    )

    budget = float(workspace_row["monthly_budget_usd"]) if workspace_row else 0.0
    remaining = max(0.0, budget - spend)
    utilisation = round((spend / budget) * 100, 2) if budget > 0 else 0.0

    return {
        "workspace_id":    workspace_id,
        "budget_usd":      budget,
        "spend_usd":       spend,
        "remaining_usd":   remaining,
        "utilisation_pct": utilisation,
        "alerts":          alerts,
    }


def _zero_status(workspace_id: str) -> dict[str, Any]:
    return {
        "workspace_id":    workspace_id,
        "budget_usd":      0.0,
        "spend_usd":       0.0,
        "remaining_usd":   0.0,
        "utilisation_pct": 0.0,
        "alerts":          [],
    }
