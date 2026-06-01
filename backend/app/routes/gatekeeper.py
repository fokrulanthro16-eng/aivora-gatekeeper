"""
Gatekeeper API routes.

  GET  /health                          — liveness probe (k8s livenessProbe)
  GET  /ready                           — readiness probe (k8s readinessProbe)
  GET  /v1/gatekeeper/status            — circuit breaker + cache diagnostics
  POST /v1/gatekeeper/protect           — explicit quota check + deduction
  POST /v1/gatekeeper/simulate-request  — read-only quota probe (no deduction)

All /v1/gatekeeper/* routes are in the middleware bypass list, so they
implement their own quota logic rather than being double-charged.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.models.schemas import (
    GatekeeperStatusResponse,
    HealthResponse,
    ProtectRequest,
    ProtectResponse,
    QuotaDecision,
    SimulateRequest,
    SimulateResponse,
)
from app.services.cache import get_quota_cache
from app.services.circuit_breaker import get_circuit_breaker
from app.services.supabase_client import is_supabase_available, process_token_bucket
from app.utils.token_estimator import estimate_request_cost

log = structlog.get_logger(__name__)
router = APIRouter()

_settings = get_settings()


class ReadyResponse(BaseModel):
    ready: bool
    supabase: bool
    circuit_breaker: str


# ── /health (liveness) ────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    cb = get_circuit_breaker()
    supabase_ok = is_supabase_available()
    # Degraded if circuit is open OR if Supabase is not connected in production
    is_degraded = (
        cb.state.value == "open"
        or (not supabase_ok and not _settings.DEMO_MODE)
    )
    return HealthResponse(
        status="degraded" if is_degraded else "ok",
        version=_settings.APP_VERSION,
        env=_settings.ENV,
    )


# ── /ready (readiness) ────────────────────────────────────────────────────────

@router.get("/ready", tags=["Health"], summary="Kubernetes readiness probe.")
async def ready() -> JSONResponse:
    """
    Returns 200 when the backend can serve traffic, 503 when it cannot.

    Not-ready conditions:
      - Supabase client is not initialised AND DEMO_MODE=false
      - Circuit breaker is OPEN in production mode (Supabase actively unreachable)

    Kubernetes readinessProbe config:
        httpGet:
          path: /ready
          port: 8000
        initialDelaySeconds: 5
        periodSeconds: 10
        failureThreshold: 3
    """
    cb          = get_circuit_breaker()
    supabase_ok = is_supabase_available()
    cb_state    = cb.state.value

    ready_flag = supabase_ok or _settings.DEMO_MODE

    payload = ReadyResponse(
        ready=ready_flag,
        supabase=supabase_ok,
        circuit_breaker=cb_state,
    )
    status_code = 200 if ready_flag else 503
    return JSONResponse(content=payload.model_dump(), status_code=status_code)


# ── /v1/gatekeeper/status ──────────────────────────────────────────────────────

@router.get(
    "/v1/gatekeeper/status",
    response_model=GatekeeperStatusResponse,
    tags=["Gatekeeper"],
)
async def gatekeeper_status() -> GatekeeperStatusResponse:
    from app.models.schemas import CacheStatsResponse, CircuitBreakerSnapshot

    cb_snap = get_circuit_breaker().snapshot()
    cache_stats = await get_quota_cache().stats()

    return GatekeeperStatusResponse(
        app_name=_settings.APP_NAME,
        version=_settings.APP_VERSION,
        env=_settings.ENV,
        supabase_available=is_supabase_available(),
        demo_mode=_settings.DEMO_MODE,
        circuit_breaker=CircuitBreakerSnapshot(**cb_snap),
        cache=CacheStatsResponse(
            total_entries=cache_stats.total_entries,
            max_entries=cache_stats.max_entries,
            hits=cache_stats.hits,
            misses=cache_stats.misses,
            evictions=cache_stats.evictions,
        ),
    )


# ── /v1/gatekeeper/protect ────────────────────────────────────────────────────

@router.post(
    "/v1/gatekeeper/protect",
    response_model=ProtectResponse,
    tags=["Gatekeeper"],
    summary="Gate an upstream LLM request (deducts tokens on allow).",
)
async def protect(payload: ProtectRequest, request: Request) -> ProtectResponse:
    """
    Use this endpoint when you want the gatekeeper to act as a standalone
    rate-limit oracle: call it before forwarding to your LLM provider, and
    only proceed if ``decision.allowed == true``.

    Tokens **are** deducted from the user's bucket on a positive decision.
    """
    user_uuid = str(payload.user_uuid)
    cache = get_quota_cache()

    # ── Cost resolution ────────────────────────────────────────────────────────
    if payload.cost_override is not None:
        cost = payload.cost_override
    else:
        cost = estimate_request_cost(
            payload.body,
            chars_per_token=_settings.TOKEN_CHARS_PER_TOKEN,
            default_cost=_settings.TOKEN_DEFAULT_COST,
            max_cost=_settings.TOKEN_MAX_COST,
        )

    # ── Cache fast-path: skip RPC for recently blocked users ──────────────────
    negative_key = f"blocked:{user_uuid}"
    cached_block: str | None = await cache.get(negative_key)
    if cached_block is not None:
        log.info("protect_cache_reject", user_uuid=user_uuid, reason=cached_block)
        return ProtectResponse(
            decision=QuotaDecision(
                allowed=False,
                remaining_tokens=0.0,
                reason=cached_block,
                estimated_cost=cost,
            ),
            user_uuid=user_uuid,
            cache_hit=True,
        )

    # ── Call Supabase RPC via circuit breaker ──────────────────────────────────
    request_id = (
        payload.request_id
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Correlation-ID")
    )

    try:
        raw: dict[str, Any] = await process_token_bucket(
            user_uuid=user_uuid,
            request_cost=cost,
            endpoint=payload.endpoint,
            http_method=payload.http_method,
            request_id=request_id,
        )
    except Exception as exc:
        log.error("protect_rpc_error", user_uuid=user_uuid, error=str(exc))
        raise HTTPException(status_code=503, detail="Quota service unavailable.") from exc

    allowed: bool = raw.get("allowed", False)
    reason: str = raw.get("reason", "unknown")
    remaining: float = float(raw.get("remaining_tokens", 0))
    degraded: bool = reason in ("circuit_open_degraded_mode", "error_fail_open")

    # ── Update cache ───────────────────────────────────────────────────────────
    if not allowed:
        await cache.set(negative_key, reason, ttl=_settings.CACHE_NEGATIVE_TTL_SECONDS)
    elif remaining >= 0:
        await cache.set(f"quota:{user_uuid}", remaining, ttl=_settings.CACHE_DEFAULT_TTL_SECONDS)

    log.info(
        "protect_decision",
        user_uuid=user_uuid,
        allowed=allowed,
        reason=reason,
        cost=cost,
        remaining=remaining,
    )

    return ProtectResponse(
        decision=QuotaDecision(
            allowed=allowed,
            remaining_tokens=remaining,
            reason=reason,
            estimated_cost=cost,
            degraded_mode=degraded,
        ),
        user_uuid=user_uuid,
        cache_hit=False,
    )


# ── /v1/gatekeeper/simulate-request ───────────────────────────────────────────

@router.post(
    "/v1/gatekeeper/simulate-request",
    response_model=SimulateResponse,
    tags=["Gatekeeper"],
    summary="Read-only quota probe — does NOT deduct tokens.",
)
async def simulate_request(payload: SimulateRequest) -> SimulateResponse:
    """
    Estimate what the gate decision *would be* for a given user and body,
    without actually consuming any tokens.

    Useful for:
    - Pre-flight checks in a UI before submitting an expensive prompt.
    - Integration tests that must not mutate quota state.
    - Budget-awareness tooling that shows remaining capacity.

    The response uses ``cached_remaining_tokens`` from the in-memory cache;
    if no recent cache entry exists, it returns ``null`` for that field
    rather than making a live DB call.
    """
    user_uuid = str(payload.user_uuid)

    cost = (
        payload.cost_override
        if payload.cost_override is not None
        else estimate_request_cost(
            payload.body,
            chars_per_token=_settings.TOKEN_CHARS_PER_TOKEN,
            default_cost=_settings.TOKEN_DEFAULT_COST,
            max_cost=_settings.TOKEN_MAX_COST,
        )
    )

    cache = get_quota_cache()

    # Check negative cache first
    cached_block: str | None = await cache.get(f"blocked:{user_uuid}")
    if cached_block is not None:
        return SimulateResponse(
            would_be_allowed=False,
            estimated_cost=cost,
            cached_remaining_tokens=0.0,
        )

    # Check positive cache for a recent token level
    cached_remaining: float | None = await cache.get(f"quota:{user_uuid}")
    if cached_remaining is not None:
        would_allow = cached_remaining >= cost
        return SimulateResponse(
            would_be_allowed=would_allow,
            estimated_cost=cost,
            cached_remaining_tokens=cached_remaining,
        )

    # No cache data — cannot predict without a live DB read
    return SimulateResponse(
        would_be_allowed=True,   # optimistic default when no data available
        estimated_cost=cost,
        cached_remaining_tokens=None,
        note="No cached quota data available; result is optimistic. Call /protect for an accurate gate.",
    )
