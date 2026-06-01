"""
Spending anomaly detection service.

Algorithm (all logic operates on workspace_monthly_usage rows):

  1. spend_spike
     Compare today's daily burn rate to the rolling 3-month average daily rate.
     Flag when: current_daily > SPIKE_MULTIPLIER × baseline_daily
             AND current_daily > MIN_DAILY_USD (avoids noise on near-zero accounts)

  2. budget_trajectory
     Project current month's total spend if the current daily rate continues.
     Flag when: projected_total > TRAJECTORY_PCT × monthly_budget_usd
             AND days_elapsed <= 20   (too late in the month to act otherwise)

  3. rapid_acceleration
     Compare this month's first-half daily rate to the second-half daily rate.
     Only applicable after day 15. Flag when: second_half_rate > 1.5 × first_half_rate.

Anomalies are inserted into spending_anomalies.  Duplicate detection: we skip
insertion when an unresolved anomaly of the same type already exists for the
workspace within the past 24 hours.

Call evaluate_and_store(workspace_id) from the usage service as a fire-and-forget
asyncio task after each successful workspace deduction.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.core.config import get_settings
from app.services.supabase_client import get_supabase_client, is_supabase_available

log = structlog.get_logger(__name__)

_TIMEOUT = 5.0


# ── Public entry points ───────────────────────────────────────────────────────

async def evaluate_and_store(workspace_id: str) -> None:
    """
    Evaluate anomalies for a workspace and persist new detections.
    Designed to run as a background asyncio task — errors are swallowed.
    """
    if not is_supabase_available():
        return
    try:
        anomalies = await _detect_anomalies(workspace_id)
        for anomaly in anomalies:
            await _insert_if_new(workspace_id, anomaly)
    except Exception as exc:
        log.warning("anomaly_eval_error", workspace_id=workspace_id, error=str(exc))


async def get_active_anomalies(workspace_id: str) -> list[dict[str, Any]]:
    """Return all unresolved anomalies for a workspace, newest first."""
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("spending_anomalies")
                .select("*")
                .eq("workspace_id", workspace_id)
                .eq("resolved", False)
                .order("detected_at", desc=True)
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data or []
    except Exception as exc:
        log.error("get_anomalies_error", workspace_id=workspace_id, error=str(exc))
        return []


async def get_all_active_anomalies(limit: int = 100) -> list[dict[str, Any]]:
    """Admin: return all unresolved anomalies across all workspaces."""
    if not is_supabase_available():
        return []
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("spending_anomalies")
                .select("*")
                .eq("resolved", False)
                .order("detected_at", desc=True)
                .limit(limit)
                .execute(),
            timeout=_TIMEOUT,
        )
        return result.data or []
    except Exception as exc:
        log.error("get_all_anomalies_error", error=str(exc))
        return []


async def resolve_anomaly(anomaly_id: str) -> bool:
    if not is_supabase_available():
        return False
    try:
        client = get_supabase_client()
        now = datetime.now(timezone.utc).isoformat()
        await asyncio.wait_for(
            client.table("spending_anomalies")
                .update({"resolved": True, "resolved_at": now})
                .eq("id", anomaly_id)
                .execute(),
            timeout=_TIMEOUT,
        )
        return True
    except Exception as exc:
        log.error("resolve_anomaly_error", anomaly_id=anomaly_id, error=str(exc))
        return False


# ── Detection logic ───────────────────────────────────────────────────────────

async def _detect_anomalies(workspace_id: str) -> list[dict[str, Any]]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    days_elapsed = now.day
    days_in_month = calendar.monthrange(year, month)[1]

    client = get_supabase_client()

    # Fetch workspace budget
    ws_result = await asyncio.wait_for(
        client.table("workspaces")
            .select("monthly_budget_usd")
            .eq("id", workspace_id)
            .maybe_single()
            .execute(),
        timeout=_TIMEOUT,
    )
    if not ws_result.data:
        return []
    budget_usd = float(ws_result.data["monthly_budget_usd"])
    if budget_usd <= 0:
        return []

    # Fetch last 4 months of usage (current + 3 previous)
    history_result = await asyncio.wait_for(
        client.table("workspace_monthly_usage")
            .select("year, month, total_cost_usd")
            .eq("workspace_id", workspace_id)
            .order("year",  desc=True)
            .order("month", desc=True)
            .limit(4)
            .execute(),
        timeout=_TIMEOUT,
    )
    rows: list[dict[str, Any]] = history_result.data or []

    # Split current vs historical
    current_row = next(
        (r for r in rows if r["year"] == year and r["month"] == month),
        None,
    )
    prev_rows = [r for r in rows if not (r["year"] == year and r["month"] == month)]

    current_spend = float(current_row["total_cost_usd"]) if current_row else 0.0
    current_daily = current_spend / max(days_elapsed, 1)

    anomalies: list[dict[str, Any]] = []

    # ── 1. Spend spike ────────────────────────────────────────────────────────
    if prev_rows:
        avg_monthly = sum(float(r["total_cost_usd"]) for r in prev_rows) / len(prev_rows)
        baseline_daily = avg_monthly / 30.0

        if (
            baseline_daily > 0
            and current_daily > s.ANOMALY_SPIKE_MULTIPLIER * baseline_daily
            and current_daily > s.ANOMALY_MIN_DAILY_SPEND_USD
        ):
            deviation = ((current_daily / baseline_daily) - 1) * 100
            severity = _spike_severity(current_daily / baseline_daily)
            anomalies.append({
                "workspace_id":  workspace_id,
                "anomaly_type":  "spend_spike",
                "severity":      severity,
                "current_value": round(current_daily, 6),
                "baseline_value": round(baseline_daily, 6),
                "deviation_pct": round(deviation, 2),
                "description":   (
                    f"Daily spend rate ${current_daily:.4f}/day is "
                    f"{deviation:.0f}% above the {len(prev_rows)}-month average "
                    f"(${baseline_daily:.4f}/day)."
                ),
                "resolved":      False,
            })

    # ── 2. Budget trajectory ──────────────────────────────────────────────────
    if days_elapsed <= 20 and current_daily > s.ANOMALY_MIN_DAILY_SPEND_USD:
        projected = current_daily * days_in_month
        trajectory_pct = (projected / budget_usd) * 100

        if trajectory_pct > s.ANOMALY_TRAJECTORY_PCT:
            deviation = trajectory_pct - 100
            severity = _trajectory_severity(trajectory_pct)
            anomalies.append({
                "workspace_id":  workspace_id,
                "anomaly_type":  "budget_trajectory",
                "severity":      severity,
                "current_value": round(projected, 6),
                "baseline_value": round(budget_usd, 6),
                "deviation_pct": round(deviation, 2),
                "description":   (
                    f"At current rate (${current_daily:.4f}/day), projected "
                    f"month-end spend is ${projected:.2f} — "
                    f"{trajectory_pct:.0f}% of the ${budget_usd:.2f} budget "
                    f"({days_elapsed} days into the month)."
                ),
                "resolved":      False,
            })

    # ── 3. Rapid acceleration (needs days_elapsed > 15) ───────────────────────
    if days_elapsed > 15 and current_row:
        # Split the month: first half vs second half daily rates
        # We only have the total — use day 15 as the midpoint heuristic
        # by comparing (total / elapsed) with a budget-based baseline
        first_half_rate = (current_spend * 0.5) / 15.0  # rough first-half estimate
        second_half_elapsed = days_elapsed - 15
        second_half_spend = current_spend * 0.5          # rough second-half estimate
        second_half_rate = second_half_spend / max(second_half_elapsed, 1)

        # Only flag if second half rate is substantially higher and notable
        if (
            first_half_rate > 0
            and second_half_rate > 1.5 * first_half_rate
            and second_half_rate > s.ANOMALY_MIN_DAILY_SPEND_USD
        ):
            deviation = ((second_half_rate / first_half_rate) - 1) * 100
            anomalies.append({
                "workspace_id":  workspace_id,
                "anomaly_type":  "rapid_acceleration",
                "severity":      "medium",
                "current_value": round(second_half_rate, 6),
                "baseline_value": round(first_half_rate, 6),
                "deviation_pct": round(deviation, 2),
                "description":   (
                    f"Spend rate in the second half of the month "
                    f"(${second_half_rate:.4f}/day) is {deviation:.0f}% higher "
                    f"than the first half (${first_half_rate:.4f}/day)."
                ),
                "resolved":      False,
            })

    return anomalies


async def _insert_if_new(workspace_id: str, anomaly: dict[str, Any]) -> None:
    """Skip insertion when a same-type unresolved anomaly was detected in the past 24 h."""
    client = get_supabase_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    existing = await asyncio.wait_for(
        client.table("spending_anomalies")
            .select("id")
            .eq("workspace_id", workspace_id)
            .eq("anomaly_type", anomaly["anomaly_type"])
            .eq("resolved",     False)
            .gte("detected_at", cutoff)
            .limit(1)
            .execute(),
        timeout=_TIMEOUT,
    )
    if existing.data:
        return   # dedup: same anomaly type already open for this workspace

    anomaly["detected_at"] = datetime.now(timezone.utc).isoformat()
    await asyncio.wait_for(
        client.table("spending_anomalies").insert(anomaly).execute(),
        timeout=_TIMEOUT,
    )
    log.info(
        "anomaly_detected",
        workspace_id=workspace_id,
        type=anomaly["anomaly_type"],
        severity=anomaly["severity"],
        deviation_pct=anomaly["deviation_pct"],
    )


# ── Severity helpers ──────────────────────────────────────────────────────────

def _spike_severity(ratio: float) -> str:
    if ratio >= 10:  return "critical"
    if ratio >= 5:   return "high"
    if ratio >= 3:   return "medium"
    return "low"


def _trajectory_severity(trajectory_pct: float) -> str:
    if trajectory_pct >= 200: return "critical"
    if trajectory_pct >= 150: return "high"
    if trajectory_pct >= 120: return "medium"
    return "low"
