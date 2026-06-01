"""
Aggregator routes — AI proxy, usage check, system status, and Polar webhooks.

  GET  /v1/aggregator/status           — full system readiness + live stats
  POST /v1/aggregator/check-usage      — quota probe without forwarding to provider
  POST /v1/aggregator/proxy-openrouter — gate + forward to OpenRouter
  POST /v1/webhooks/polar              — Polar.sh subscription lifecycle events
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.models.aggregator_schemas import (
    AggregatorStats,
    AggregatorStatusResponse,
    CheckUsageRequest,
    CheckUsageResponse,
    PolarWebhookResponse,
    ProxyOpenRouterRequest,
    ProxyOpenRouterResponse,
    UsageInfo,
)
from app.services.cache import get_quota_cache
from app.services.circuit_breaker import get_circuit_breaker
from app.services.openrouter import (
    OpenRouterError,
    call_openrouter,
    estimate_openrouter_cost,
    is_openrouter_configured,
)
from app.services.polar import (
    PolarSignatureError,
    build_subscription_upsert,
    is_polar_configured,
    verify_webhook_signature,
)
from app.services.supabase_client import get_supabase_client, is_supabase_available
from app.services.usage import check_ai_usage

log = structlog.get_logger(__name__)
router = APIRouter()

_settings = get_settings()


async def _count_active_tiers() -> int:
    """Query billing_tiers for the number of active tiers; returns 0 on error."""
    if not is_supabase_available():
        return 0
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("billing_tiers")
                .select("id", count="exact")
                .eq("is_active", True)
                .execute(),
            timeout=2.0,
        )
        return result.count or 0
    except Exception:
        return 0


# ── /v1/aggregator/status ─────────────────────────────────────────────────────

@router.get(
    "/v1/aggregator/status",
    response_model=AggregatorStatusResponse,
    tags=["Aggregator"],
)
async def aggregator_status() -> AggregatorStatusResponse:
    cb = get_circuit_breaker()
    supabase_ok = is_supabase_available()
    openrouter_ok = is_openrouter_configured()
    polar_ok = is_polar_configured()
    cache_stats, active_tiers = await asyncio.gather(
        get_quota_cache().stats(),
        _count_active_tiers(),
    )
    cb_snap = cb.snapshot()

    # Derive top-level status
    if supabase_ok and cb_snap["state"] == "closed":
        status = "protected"
    elif not supabase_ok:
        status = "supabase_not_connected"
    else:
        status = "firewall_off"

    # Compute stats from in-memory circuit breaker counters
    # (These reset on server restart; a persistent stats endpoint can be added later.)
    blocked = cb_snap["total_fallbacks"]
    # Rough money-saved estimate: each blocked fallback prevented ~$0.02 average request
    cost_saved = round(blocked * 0.02, 4)

    return AggregatorStatusResponse(
        app_name=_settings.APP_NAME,
        version=_settings.APP_VERSION,
        env=_settings.ENV,
        status=status,
        supabase_available=supabase_ok,
        openrouter_configured=openrouter_ok,
        polar_configured=polar_ok,
        demo_mode=_settings.DEMO_MODE,
        stats=AggregatorStats(
            total_calls_blocked=blocked,
            total_cost_saved_usd=cost_saved,
            active_sessions=cache_stats.total_entries,
            active_tiers=active_tiers,
        ),
        circuit_breaker_state=cb_snap["state"],
        cache_entries=cache_stats.total_entries,
    )


# ── /v1/aggregator/check-usage ────────────────────────────────────────────────

@router.post(
    "/v1/aggregator/check-usage",
    response_model=CheckUsageResponse,
    tags=["Aggregator"],
    summary="Pre-flight quota check without forwarding to any LLM provider.",
)
async def check_usage(payload: CheckUsageRequest) -> CheckUsageResponse:
    """
    Performs the full quota check (subscription tier, message limit, budget,
    token bucket) and deducts usage if allowed.

    Use this from a Next.js API route to gate requests before forwarding
    to OpenRouter yourself:

        // pages/api/chat.ts
        const gate = await fetch('/v1/aggregator/check-usage', { ... })
        if (!gate.decision.allowed) return res.status(429).json(gate)
        // proceed to call OpenRouter
    """
    user_uuid = str(payload.user_uuid)
    cost = payload.cost_override if payload.cost_override is not None else payload.estimated_cost

    result: dict[str, Any] = await check_ai_usage(
        user_uuid=user_uuid,
        provider=payload.provider,
        model=payload.model,
        estimated_tokens=payload.estimated_tokens,
        estimated_cost=cost,
    )

    return CheckUsageResponse(
        allowed=result.get("allowed", False),
        reason=result.get("reason", "unknown"),
        remaining_messages=int(result.get("remaining_messages", 0)),
        remaining_budget_usd=float(result.get("remaining_budget_usd", 0)),
        estimated_cost=float(result.get("estimated_cost", cost)),
        provider=result.get("provider", payload.provider),
        model=result.get("model", payload.model),
        user_uuid=user_uuid,
    )


# ── /v1/aggregator/proxy-openrouter ──────────────────────────────────────────

@router.post(
    "/v1/aggregator/proxy-openrouter",
    response_model=ProxyOpenRouterResponse,
    tags=["Aggregator"],
    summary="Gate-and-forward: quota check then OpenRouter call.",
)
async def proxy_openrouter(payload: ProxyOpenRouterRequest) -> ProxyOpenRouterResponse:
    """
    The complete billing-firewall proxy:

      1. Estimate token cost (input tokens + max_tokens output budget).
      2. Call check_and_consume_ai_usage — deduct from quota atomically.
      3. If quota denied: return structured 429 error; OpenRouter is never called.
      4. If quota allowed: forward to OpenRouter.
      5. Return the OpenRouter response with usage metadata.

    Set dry_run=true to perform only steps 1–3 without forwarding (step 4).
    This is equivalent to /check-usage but uses the full message body for
    accurate token estimation.
    """
    user_uuid = str(payload.user_uuid)
    messages_raw = [m.model_dump() for m in payload.messages]

    # ── 1. Estimate cost ───────────────────────────────────────────────────────
    estimated_cost, estimated_tokens, provider, model = estimate_openrouter_cost(
        payload.model, messages_raw, payload.max_tokens
    )

    # ── 2. Quota check + atomic deduction ─────────────────────────────────────
    result: dict[str, Any] = await check_ai_usage(
        user_uuid=user_uuid,
        provider=provider,
        model=model,
        estimated_tokens=estimated_tokens,
        estimated_cost=estimated_cost,
    )

    allowed: bool = result.get("allowed", False)
    reason: str = result.get("reason", "unknown")
    remaining_msgs = int(result.get("remaining_messages", 0))
    remaining_budget = float(result.get("remaining_budget_usd", 0))

    usage = UsageInfo(
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost,
        remaining_messages=remaining_msgs,
        remaining_budget_usd=remaining_budget,
        provider=provider,
        model=model,
    )

    # ── 3. Reject if quota denied ─────────────────────────────────────────────
    if not allowed:
        _error_codes = {
            "monthly_message_limit_exceeded": ("MESSAGE_LIMIT_EXCEEDED", 429),
            "monthly_budget_exceeded":        ("BUDGET_EXCEEDED",        429),
            "token_bucket_exhausted":         ("RATE_LIMIT_EXCEEDED",    429),
            "account_suspended":              ("ACCOUNT_SUSPENDED",      403),
            "quota_not_found":                ("QUOTA_NOT_PROVISIONED",  422),
            "supabase_unavailable":           ("QUOTA_SERVICE_ERROR",    503),
        }
        code, http_status = _error_codes.get(reason, ("QUOTA_REJECTED", 429))
        raise HTTPException(
            status_code=http_status,
            detail={
                "error": {
                    "code": code,
                    "message": f"Request blocked: {reason.replace('_', ' ')}.",
                    "details": {
                        "reason":               reason,
                        "estimated_cost_usd":   estimated_cost,
                        "remaining_messages":   remaining_msgs,
                        "remaining_budget_usd": remaining_budget,
                        "provider":             provider,
                        "model":                model,
                    },
                }
            },
        )

    # ── 4. Dry run — quota deducted but no provider call ──────────────────────
    if payload.dry_run:
        return ProxyOpenRouterResponse(
            allowed=True,
            reason="dry_run_allowed",
            usage=usage,
            openrouter_response=None,
        )

    # ── 5. Forward to OpenRouter ──────────────────────────────────────────────
    try:
        or_response = await call_openrouter(
            model=payload.model,
            messages=messages_raw,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
        )
    except OpenRouterError as exc:
        log.error(
            "openrouter_proxy_error",
            user_uuid=user_uuid,
            model=payload.model,
            status=exc.status_code,
            error=exc.message,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "OPENROUTER_ERROR",
                    "message": exc.message,
                    "details": exc.body,
                }
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "OPENROUTER_NOT_CONFIGURED", "message": str(exc)}},
        ) from exc

    return ProxyOpenRouterResponse(
        allowed=True,
        reason="allowed",
        usage=usage,
        openrouter_response=or_response,
    )


# ── /v1/webhooks/polar ────────────────────────────────────────────────────────

@router.post(
    "/v1/webhooks/polar",
    response_model=PolarWebhookResponse,
    tags=["Webhooks"],
    summary="Receive Polar.sh subscription lifecycle events.",
)
async def polar_webhook(request: Request) -> PolarWebhookResponse:
    """
    Idempotent webhook receiver for Polar.sh events.

    On receipt:
      1. Verify the webhook-signature header.
      2. Persist the raw event to polar_webhook_events (idempotent on event_id).
      3. Process subscription.* events: upsert into subscriptions table and
         update user_quotas.billing_tier_id.

    Returns 200 immediately.  Failures during step 3 are logged and stored as
    errors in polar_webhook_events for later replay — they do NOT cause a non-200
    response (Polar would re-deliver and create duplicate processing).
    """
    # ── Read raw body and headers ──────────────────────────────────────────────
    raw_body = await request.body()
    event_id    = request.headers.get("webhook-id", "")
    timestamp   = request.headers.get("webhook-timestamp", "")
    signature   = request.headers.get("webhook-signature", "")

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing webhook-id header.")

    # ── Verify signature ───────────────────────────────────────────────────────
    try:
        verify_webhook_signature(raw_body, event_id, timestamp, signature)
    except PolarSignatureError as exc:
        log.warning("polar_webhook_signature_failed", event_id=event_id, error=str(exc))
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc

    event_type: str = payload.get("type", "unknown")

    # ── Persist event idempotently ─────────────────────────────────────────────
    processed = False
    error_msg: str | None = None

    if is_supabase_available():
        try:
            client = get_supabase_client()
            await asyncio.wait_for(
                client.table("polar_webhook_events").upsert(
                    {
                        "event_id":   event_id,
                        "event_type": event_type,
                        "payload":    payload,
                        "processed":  False,
                    },
                    on_conflict="event_id",
                    ignore_duplicates=True,
                ).execute(),
                timeout=5.0,
            )
        except Exception as exc:
            log.error("polar_webhook_persist_error", event_id=event_id, error=str(exc))
            # Non-fatal: still attempt to process

        # ── Process subscription events ────────────────────────────────────────
        data = payload.get("data", {})
        # Polar includes the customer metadata in the event payload
        metadata = data.get("metadata", {}) or data.get("customer", {}).get("metadata", {}) or {}
        user_uuid: str | None = (
            metadata.get("supabase_user_id")
            or metadata.get("user_id")
            or data.get("user_id")
        )

        upsert_data = build_subscription_upsert(user_uuid or "", event_type, payload)

        if upsert_data and user_uuid:
            try:
                await asyncio.wait_for(
                    client.table("subscriptions").upsert(
                        upsert_data,
                        on_conflict="user_uuid",
                    ).execute(),
                    timeout=5.0,
                )
                # Sync billing_tier_id on user_quotas
                await asyncio.wait_for(
                    client.table("user_quotas").update(
                        {"billing_tier_id": upsert_data["tier_id"]}
                    ).eq("user_uuid", user_uuid).execute(),
                    timeout=5.0,
                )
                # Mark event as processed
                await asyncio.wait_for(
                    client.table("polar_webhook_events").update(
                        {
                            "processed": True,
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                            "user_uuid": user_uuid,
                        }
                    ).eq("event_id", event_id).execute(),
                    timeout=5.0,
                )
                processed = True
                log.info(
                    "polar_webhook_processed",
                    event_id=event_id,
                    event_type=event_type,
                    user_uuid=user_uuid,
                    tier_id=upsert_data["tier_id"],
                )
            except Exception as exc:
                error_msg = str(exc)
                log.error(
                    "polar_webhook_process_error",
                    event_id=event_id,
                    event_type=event_type,
                    error=error_msg,
                )
                try:
                    await asyncio.wait_for(
                        client.table("polar_webhook_events").update(
                            {"error": error_msg[:1000]}
                        ).eq("event_id", event_id).execute(),
                        timeout=3.0,
                    )
                except Exception:
                    pass
        else:
            # Non-subscription event or no user_uuid — nothing to process
            processed = True

    else:
        log.warning(
            "polar_webhook_supabase_unavailable",
            event_id=event_id,
            event_type=event_type,
        )

    return PolarWebhookResponse(
        received=True,
        event_id=event_id,
        event_type=event_type,
        processed=processed,
        error=error_msg,
    )
