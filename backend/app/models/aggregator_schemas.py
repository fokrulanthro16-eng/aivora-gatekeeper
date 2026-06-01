"""
Pydantic v2 models for the /v1/aggregator/* and /v1/webhooks/polar routes.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── /v1/aggregator/status ─────────────────────────────────────────────────────

class AggregatorStats(BaseModel):
    """Aggregated runtime counters derived from in-memory circuit-breaker state."""
    total_calls_blocked: int
    total_cost_saved_usd: float
    active_sessions: int
    active_tiers: int


class AggregatorStatusResponse(BaseModel):
    app_name: str
    version: str
    env: str
    # System-level readiness
    status: Literal["protected", "supabase_not_connected", "firewall_off"]
    supabase_available: bool
    openrouter_configured: bool
    polar_configured: bool
    demo_mode: bool
    # Runtime stats (derived from in-memory state; reset on restart)
    stats: AggregatorStats
    # Raw subsystem snapshots
    circuit_breaker_state: str
    cache_entries: int


# ── /v1/aggregator/check-usage ────────────────────────────────────────────────

class CheckUsageRequest(BaseModel):
    user_uuid: UUID = Field(..., description="Authenticated user's UUID from Supabase auth.")
    provider: str   = Field(..., min_length=1, max_length=64, description="LLM provider slug, e.g. 'openai'.")
    model: str      = Field(..., min_length=1, max_length=128, description="Model name, e.g. 'gpt-4o'.")
    estimated_tokens: int   = Field(default=500, ge=1, le=200_000)
    estimated_cost: float   = Field(default=0.005, ge=0.0)
    # Optional: bypass cost estimation and use a caller-supplied value
    cost_override: float | None = Field(default=None, ge=0.0)

    @field_validator("provider", "model")
    @classmethod
    def lower_strip(cls, v: str) -> str:
        return v.lower().strip()


class CheckUsageResponse(BaseModel):
    allowed: bool
    reason: str
    remaining_messages: int
    remaining_budget_usd: float
    estimated_cost: float
    provider: str
    model: str
    user_uuid: str


# ── /v1/aggregator/proxy-openrouter ──────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str = Field(..., min_length=1)


class ProxyOpenRouterRequest(BaseModel):
    """
    Gate-and-forward request.  The gatekeeper will:
      1. Estimate token cost for this model + messages.
      2. Call check_and_consume_ai_usage to deduct from the user's quota.
      3. Only forward to OpenRouter if quota allows.
    """
    user_uuid: UUID
    model: str = Field(..., min_length=1, max_length=128,
                       description="OpenRouter model key, e.g. 'openai/gpt-4o'.")
    messages: list[ChatMessage] = Field(..., min_length=1)
    max_tokens: int = Field(default=1024, ge=1, le=32_768)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # If true, only perform the quota check — do NOT forward to OpenRouter.
    # Useful for pre-flight validation from a Next.js API route.
    dry_run: bool = False

    @field_validator("model")
    @classmethod
    def normalise_model(cls, v: str) -> str:
        return v.lower().strip()


class UsageInfo(BaseModel):
    estimated_tokens: int
    estimated_cost_usd: float
    remaining_messages: int
    remaining_budget_usd: float
    provider: str
    model: str


class ProxyOpenRouterResponse(BaseModel):
    # Gate decision
    allowed: bool
    reason: str
    usage: UsageInfo
    # OpenRouter response — None when allowed=False or dry_run=True
    openrouter_response: dict[str, Any] | None = None


# ── /v1/webhooks/polar ────────────────────────────────────────────────────────

class PolarWebhookResponse(BaseModel):
    received: bool
    event_id: str
    event_type: str
    processed: bool
    error: str | None = None
