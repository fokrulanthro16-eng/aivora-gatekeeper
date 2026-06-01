"""
Pydantic v2 request / response models for all Gatekeeper endpoints.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Shared primitives ──────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── /health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str


# ── /v1/gatekeeper/status ──────────────────────────────────────────────────────

class CacheStatsResponse(BaseModel):
    total_entries: int
    max_entries: int
    hits: int
    misses: int
    evictions: int


class CircuitBreakerSnapshot(BaseModel):
    name: str
    state: str
    failure_count: int
    half_open_successes: int
    opened_at: float | None
    last_failure_at: float | None
    total_calls: int
    total_successes: int
    total_failures: int
    total_fallbacks: int


class GatekeeperStatusResponse(BaseModel):
    app_name: str
    version: str
    env: str
    # True when a live Supabase client is connected and ready.
    # False means all quota checks are handled by the circuit-breaker fallback.
    supabase_available: bool
    # True when the server was started with DEMO_MODE=true.
    # False in all production deployments.
    demo_mode: bool
    circuit_breaker: CircuitBreakerSnapshot
    cache: CacheStatsResponse


# ── /v1/gatekeeper/protect ────────────────────────────────────────────────────

class ProtectRequest(BaseModel):
    """
    Describes an upstream LLM request that the caller wants gated.

    Supply *body* to let the estimator compute the cost automatically, or set
    *cost_override* to bypass estimation entirely (useful for batch jobs with a
    known cost).
    """
    user_uuid: UUID = Field(..., description="The authenticated user's UUID from Supabase auth.")
    endpoint: str | None = Field(default=None, max_length=256, description="Target endpoint label, e.g. '/v1/chat/completions'.")
    http_method: str | None = Field(default="POST", max_length=10)
    request_id: str | None = Field(default=None, max_length=128, description="Caller-supplied correlation ID for distributed tracing.")
    body: dict[str, Any] | None = Field(default=None, description="LLM request body used for cost estimation.")
    cost_override: int | None = Field(default=None, ge=1, le=10_000, description="Explicit token cost; skips body estimation when set.")

    @field_validator("http_method")
    @classmethod
    def uppercase_method(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class QuotaDecision(BaseModel):
    allowed: bool
    remaining_tokens: float
    reason: str
    estimated_cost: int
    degraded_mode: bool = False


class ProtectResponse(BaseModel):
    decision: QuotaDecision
    user_uuid: str
    cache_hit: bool


# ── /v1/gatekeeper/simulate-request ───────────────────────────────────────────

class SimulateRequest(BaseModel):
    """Read-only quota probe — does NOT deduct tokens."""
    user_uuid: UUID
    body: dict[str, Any] | None = None
    cost_override: int | None = Field(default=None, ge=1, le=10_000)
    endpoint: str | None = Field(default=None, max_length=256)


class SimulateResponse(BaseModel):
    would_be_allowed: bool
    estimated_cost: int
    cached_remaining_tokens: float | None
    note: str = "Simulation only — no tokens were deducted."
