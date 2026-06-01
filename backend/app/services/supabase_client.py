"""
Async Supabase client wrapper.

The supabase-py v2 async client is created once at application startup
(via init_supabase_client) and stored as a module-level singleton.  All
callers use get_supabase_client() to obtain the shared instance.

process_token_bucket() wraps the RPC call with circuit-breaker protection.

Fallback behaviour depends on DEMO_MODE:
  DEMO_MODE=false (production default)
    → fallback returns allowed=False / reason=supabase_unavailable
    → requests are rejected when Supabase is unreachable
  DEMO_MODE=true
    → fallback returns allowed=True / reason=circuit_open_degraded_mode
    → requests are allowed through with a degraded-mode marker
"""
from __future__ import annotations

import asyncio
import structlog
from typing import Any

from supabase import AsyncClient, acreate_client

log = structlog.get_logger(__name__)

_client: AsyncClient | None = None
_init_lock = asyncio.Lock()


def is_supabase_available() -> bool:
    """Returns True if the Supabase client was successfully initialised."""
    return _client is not None


async def init_supabase_client() -> None:
    """
    Initialise the module-level async Supabase client at application startup.

    Production (DEMO_MODE=false):
      Missing key → logs a structured ERROR and leaves _client as None.
      All subsequent RPC calls will be rejected (allowed=False).

    Demo mode (DEMO_MODE=true):
      Missing key → logs a WARNING and leaves _client as None.
      All subsequent RPC calls will use the fail-open fallback (allowed=True).
    """
    global _client
    from app.core.config import get_settings

    s = get_settings()

    if not s.SUPABASE_SERVICE_ROLE_KEY:
        if s.DEMO_MODE:
            log.warning(
                "supabase_key_missing_demo",
                msg=(
                    "SUPABASE_SERVICE_ROLE_KEY not set. "
                    "Running in DEMO_MODE — all quota checks will be skipped (fail-open)."
                ),
            )
        else:
            log.error(
                "supabase_key_missing_production",
                msg=(
                    "SUPABASE_SERVICE_ROLE_KEY is not configured. "
                    "Set it in your .env file or environment. "
                    "All quota checks will REJECT requests until the key is provided."
                ),
                supabase_url=s.SUPABASE_URL,
                demo_mode=False,
            )
        return  # _client stays None; circuit breaker fallback handles all calls

    async with _init_lock:
        _client = await acreate_client(s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY)
    log.info("supabase_client_ready", url=s.SUPABASE_URL)


def get_supabase_client() -> AsyncClient:
    if _client is None:
        raise RuntimeError(
            "Supabase client is not initialised. "
            "Ensure SUPABASE_SERVICE_ROLE_KEY is set in your environment."
        )
    return _client


# ── Token bucket RPC ───────────────────────────────────────────────────────────

async def _rpc_token_bucket(
    user_uuid: str,
    request_cost: int,
    endpoint: str | None,
    http_method: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    """
    Calls the process_token_bucket_leak PL/pgSQL function via Supabase RPC.
    Raises on any network / HTTP error so the circuit breaker counts it.
    """
    client = get_supabase_client()
    from app.core.config import get_settings

    timeout = get_settings().SUPABASE_RPC_TIMEOUT_SECONDS

    response = await asyncio.wait_for(
        client.rpc(
            "process_token_bucket_leak",
            {
                "p_user_uuid": user_uuid,
                "p_request_cost": request_cost,
                "p_endpoint": endpoint,
                "p_http_method": http_method,
                "p_request_id": request_id,
            },
        ).execute(),
        timeout=timeout,
    )
    return response.data  # type: ignore[return-value]


async def _fallback_token_bucket(
    user_uuid: str,
    request_cost: int,
    endpoint: str | None,
    http_method: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    """
    Fallback invoked when the circuit breaker is OPEN (Supabase unreachable
    or not configured).

    Production (DEMO_MODE=false): reject the request with a clear reason so
    the caller knows Supabase is required.

    Demo mode (DEMO_MODE=true): allow the request through with a degraded
    marker so local development works without a live database.
    """
    from app.core.config import get_settings

    demo = get_settings().DEMO_MODE

    if demo:
        log.warning(
            "circuit_open_fallback_demo",
            user_uuid=user_uuid,
            request_cost=request_cost,
            endpoint=endpoint,
        )
        return {
            "allowed": True,
            "remaining_tokens": -1,
            "reason": "circuit_open_degraded_mode",
        }

    log.error(
        "circuit_open_fallback_production",
        user_uuid=user_uuid,
        request_cost=request_cost,
        endpoint=endpoint,
        msg="Supabase unavailable in production mode — request rejected.",
    )
    return {
        "allowed": False,
        "remaining_tokens": 0,
        "reason": "supabase_unavailable",
    }


async def process_token_bucket(
    user_uuid: str,
    request_cost: int,
    endpoint: str | None = None,
    http_method: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Public entry point.  Wraps the Supabase RPC call with the circuit breaker.
    Returns the JSON payload from process_token_bucket_leak, or the mode-aware
    fallback dict when the breaker is OPEN.
    """
    from app.services.circuit_breaker import get_circuit_breaker

    cb = get_circuit_breaker()
    return await cb.call(
        _rpc_token_bucket,
        _fallback_token_bucket,
        user_uuid,
        request_cost,
        endpoint,
        http_method,
        request_id,
    )
