"""
API Gateway Middleware

Intercepts every non-bypass request and enforces token-bucket quota:

  1. Validate X-User-UUID header (UUID4 format).
  2. Read the request body once; Starlette caches it so downstream handlers
     can read it again without a second stream consumption.
  3. Check the in-memory cache for a recent negative (blocked) decision.
     Cache hit on a block → reject immediately without hitting Supabase.
  4. Call Supabase RPC process_token_bucket_leak (via circuit breaker).
  5. Cache the positive token level briefly to inform rapid subsequent checks.
  6. Return standardised JSON error responses on 401 / 429 / 503.
  7. Attach X-RateLimit-* headers and a degraded-mode warning when the
     circuit breaker is OPEN.

Bypass paths (configurable via GATEWAY_BYPASS_PATHS env var) skip all steps.
"""
from __future__ import annotations

import json
import time
import uuid as _uuid_module
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.services.cache import get_quota_cache
from app.services.supabase_client import process_token_bucket
from app.utils.token_estimator import estimate_request_cost

log = structlog.get_logger(__name__)

_MISSING_UUID_RESPONSE = JSONResponse(
    status_code=401,
    content={
        "error": {
            "code": "MISSING_USER_IDENTITY",
            "message": "X-User-UUID header is required for all protected routes.",
            "details": {"header": "X-User-UUID"},
        }
    },
)

_INVALID_UUID_RESPONSE = JSONResponse(
    status_code=401,
    content={
        "error": {
            "code": "INVALID_USER_IDENTITY",
            "message": "X-User-UUID must be a valid UUID v4.",
            "details": {"header": "X-User-UUID"},
        }
    },
)


def _rate_limit_response(decision: dict[str, Any], cost: int) -> JSONResponse:
    reason = decision.get("reason", "unknown")
    remaining = decision.get("remaining_tokens", 0)

    code_map = {
        "insufficient_tokens": ("RATE_LIMIT_EXCEEDED", "Token bucket exhausted. Retry after the bucket refills."),
        "monthly_budget_exceeded": ("MONTHLY_BUDGET_EXCEEDED", "Monthly token budget consumed. Resets at the start of next calendar month."),
        "suspended": ("ACCOUNT_SUSPENDED", "This account has been administratively suspended."),
        "quota_not_found": ("QUOTA_NOT_PROVISIONED", "No quota row found. Ensure the user account is fully provisioned."),
    }
    code, message = code_map.get(reason, ("QUOTA_REJECTED", "Request rejected by quota policy."))

    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": {
                    "reason": reason,
                    "requested_cost": cost,
                    "remaining_tokens": remaining,
                },
            }
        },
        headers={
            "X-RateLimit-Remaining": str(max(0, int(remaining))),
            "Retry-After": "30",
        },
    )


def _service_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "QUOTA_SERVICE_UNAVAILABLE",
                "message": "Quota service is temporarily unavailable. Please retry shortly.",
                "details": None,
            }
        },
        headers={"Retry-After": "10"},
    )


class GatewayMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path

        # ── Bypass check ───────────────────────────────────────────────────────
        if path in self._settings.bypass_paths_set:
            return await call_next(request)

        # ── 1. Validate X-User-UUID ────────────────────────────────────────────
        raw_uuid = request.headers.get("X-User-UUID", "").strip()
        if not raw_uuid:
            return _MISSING_UUID_RESPONSE

        try:
            user_uuid = str(_uuid_module.UUID(raw_uuid))
        except ValueError:
            return _INVALID_UUID_RESPONSE

        # ── 2. Read & cache request body ───────────────────────────────────────
        # request.body() caches into request._body on first call, so downstream
        # handlers receive the same bytes without stream re-reading.
        raw_body = await request.body()
        body_dict: dict[str, Any] | None = None
        if raw_body:
            try:
                body_dict = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_dict = None

        # ── 3. Estimate cost ───────────────────────────────────────────────────
        cfg = self._settings
        cost = estimate_request_cost(
            body_dict,
            chars_per_token=cfg.TOKEN_CHARS_PER_TOKEN,
            default_cost=cfg.TOKEN_DEFAULT_COST,
            max_cost=cfg.TOKEN_MAX_COST,
        )

        cache = get_quota_cache()
        negative_key = f"blocked:{user_uuid}"
        positive_key = f"quota:{user_uuid}"

        request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")

        # ── 4. Cache: fast-reject for recently blocked users ───────────────────
        cached_block = await cache.get(negative_key)
        if cached_block is not None:
            log.info(
                "gateway_cache_reject",
                user_uuid=user_uuid,
                cost=cost,
                reason=cached_block,
            )
            return _rate_limit_response(
                {"reason": cached_block, "remaining_tokens": 0},
                cost,
            )

        # ── 5. Call Supabase token bucket (via circuit breaker) ────────────────
        t0 = time.monotonic()
        try:
            decision = await process_token_bucket(
                user_uuid=user_uuid,
                request_cost=cost,
                endpoint=path,
                http_method=request.method,
                request_id=request_id,
            )
        except Exception as exc:
            log.error("gateway_quota_error", user_uuid=user_uuid, error=str(exc))
            if cfg.GATEWAY_FAIL_OPEN:
                decision = {
                    "allowed": True,
                    "remaining_tokens": -1,
                    "reason": "error_fail_open",
                }
            else:
                return _service_unavailable_response()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        allowed: bool = decision.get("allowed", False)
        reason: str = decision.get("reason", "unknown")
        remaining: float = decision.get("remaining_tokens", 0)
        degraded: bool = reason in ("circuit_open_degraded_mode", "error_fail_open")

        log.info(
            "gateway_decision",
            user_uuid=user_uuid,
            allowed=allowed,
            reason=reason,
            cost=cost,
            remaining=remaining,
            elapsed_ms=elapsed_ms,
            path=path,
        )

        # ── 6. Cache the outcome ───────────────────────────────────────────────
        if not allowed:
            await cache.set(negative_key, reason, ttl=cfg.CACHE_NEGATIVE_TTL_SECONDS)
        elif remaining >= 0:
            # Only cache accurate readings; remaining=-1 means degraded mode.
            await cache.set(positive_key, remaining, ttl=cfg.CACHE_DEFAULT_TTL_SECONDS)

        # ── 7. Reject if not allowed ───────────────────────────────────────────
        if not allowed:
            return _rate_limit_response(decision, cost)

        # ── 8. Attach rate-limit headers and pass through ─────────────────────
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(max(0, int(remaining)))
        response.headers["X-RateLimit-Cost"] = str(cost)
        if degraded:
            response.headers["X-Gatekeeper-Degraded"] = "true"
        return response
