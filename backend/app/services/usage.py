"""
Usage counter service — wraps the workspace_check_and_consume_usage Supabase
RPC and the circuit breaker.

workspace_check_and_consume_usage() is a superset of check_and_consume_ai_usage():
  • If the calling user has a workspace_id, it enforces the workspace monthly
    budget cap FIRST, then delegates to the per-user quota checks.
  • If the user has no workspace_id, it is equivalent to the original
    check_and_consume_ai_usage() — fully backward-compatible.

After each successful workspace deduction the function schedules a background
anomaly evaluation task (fire-and-forget).

All quota decisions for AI provider calls should go through
check_ai_usage() in this module.  Never call the Supabase RPC directly
from a route handler.
"""
from __future__ import annotations

import asyncio
import structlog
from typing import Any

from app.core.config import get_settings
from app.services.supabase_client import get_supabase_client

log = structlog.get_logger(__name__)


async def _rpc_check_and_consume(
    user_uuid: str,
    provider: str,
    model: str,
    estimated_tokens: int,
    estimated_cost: float,
) -> dict[str, Any]:
    """
    Call workspace_check_and_consume_usage() — the hierarchical quota gate.

    Enforces (in order):
      1. Workspace monthly USD budget cap  (skipped when user has no workspace)
      2. User monthly message limit
      3. User monthly dollar budget
      4. User token bucket

    Raises on any network / HTTP error so the circuit breaker counts the failure.
    On a successful allow, schedules background anomaly detection.
    """
    client = get_supabase_client()
    timeout = get_settings().SUPABASE_RPC_TIMEOUT_SECONDS

    response = await asyncio.wait_for(
        client.rpc(
            "workspace_check_and_consume_usage",
            {
                "p_user_uuid":        user_uuid,
                "p_provider":         provider,
                "p_model":            model,
                "p_estimated_tokens": estimated_tokens,
                "p_estimated_cost":   estimated_cost,
            },
        ).execute(),
        timeout=timeout,
    )
    data: dict[str, Any] = response.data  # type: ignore[return-value]

    # Fire-and-forget anomaly evaluation when the request was allowed
    # and the user belongs to a workspace.
    workspace_id: str | None = data.get("workspace_id")
    if data.get("allowed") and workspace_id:
        asyncio.create_task(_run_anomaly_check(workspace_id))

    return data


async def _run_anomaly_check(workspace_id: str) -> None:
    """Background anomaly evaluation — swallows all errors."""
    try:
        from app.services.anomaly_detector import evaluate_and_store
        await evaluate_and_store(workspace_id)
    except Exception as exc:
        log.warning("anomaly_check_task_error", workspace_id=workspace_id, error=str(exc))


async def _fallback_check_usage(
    user_uuid: str,
    provider: str,
    model: str,
    estimated_tokens: int,
    estimated_cost: float,
) -> dict[str, Any]:
    """
    Circuit-breaker fallback for check_ai_usage.

    Production (DEMO_MODE=false): reject with supabase_unavailable so that
    no request reaches an LLM provider without a real quota check.

    Demo mode (DEMO_MODE=true): allow through so local development works
    without a running Supabase.
    """
    from app.core.config import get_settings as gs

    demo = gs().DEMO_MODE
    if demo:
        log.warning(
            "usage_check_fallback_demo",
            user_uuid=user_uuid,
            provider=provider,
            model=model,
        )
        return {
            "allowed":              True,
            "reason":               "circuit_open_degraded_mode",
            "remaining_messages":   -1,
            "remaining_budget_usd": -1,
            "estimated_cost":       estimated_cost,
            "provider":             provider,
            "model":                model,
        }

    log.error(
        "usage_check_fallback_production",
        user_uuid=user_uuid,
        provider=provider,
        model=model,
        msg="Supabase unavailable — AI proxy request rejected.",
    )
    return {
        "allowed":              False,
        "reason":               "supabase_unavailable",
        "remaining_messages":   0,
        "remaining_budget_usd": 0,
        "estimated_cost":       estimated_cost,
        "provider":             provider,
        "model":                model,
    }


async def check_ai_usage(
    user_uuid: str,
    provider: str,
    model: str,
    estimated_tokens: int,
    estimated_cost: float,
) -> dict[str, Any]:
    """
    Public entry point.  Wraps the Supabase RPC with circuit-breaker protection.

    Returns the JSON payload from check_and_consume_ai_usage, or the
    mode-aware fallback dict when the circuit breaker is OPEN.
    """
    from app.services.circuit_breaker import get_circuit_breaker

    cb = get_circuit_breaker()
    return await cb.call(
        _rpc_check_and_consume,
        _fallback_check_usage,
        user_uuid,
        provider,
        model,
        estimated_tokens,
        estimated_cost,
    )
