"""
Pydantic v2 models for workspace, admin, and invoice routes.

Hierarchy:
  Workspace (monthly_budget_usd cap)
    └── WorkspaceMember (user_uuid + role)
          └── Per-user quota (existing user_quotas row)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Workspace ─────────────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str               = Field(..., min_length=2, max_length=120)
    slug: str               = Field(..., min_length=3, max_length=63,
                                    pattern=r'^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$')
    owner_uuid: UUID
    plan: Literal["starter", "growth", "enterprise"] = "starter"
    monthly_budget_usd: float = Field(default=50.0, gt=0, le=1_000_000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


class WorkspaceUpdate(BaseModel):
    name: str | None               = Field(default=None, min_length=2, max_length=120)
    plan: Literal["starter", "growth", "enterprise"] | None = None
    monthly_budget_usd: float | None = Field(default=None, gt=0, le=1_000_000)


class WorkspaceRead(BaseModel):
    id: UUID
    name: str
    slug: str
    owner_uuid: UUID
    plan: str
    monthly_budget_usd: float
    is_active: bool
    is_suspended: bool
    suspension_reason: str | None = None
    created_at: datetime
    updated_at: datetime


# ── Workspace members ─────────────────────────────────────────────────────────

class WorkspaceMemberCreate(BaseModel):
    user_uuid: UUID
    role: Literal["owner", "admin", "member"] = "member"


class WorkspaceMemberRead(BaseModel):
    workspace_id: UUID
    user_uuid: UUID
    role: str
    joined_at: datetime


# ── Monthly usage ─────────────────────────────────────────────────────────────

class WorkspaceMonthlyUsageRead(BaseModel):
    workspace_id: UUID
    year: int
    month: int
    total_requests: int
    blocked_requests: int
    total_tokens: int
    total_cost_usd: float
    last_request_at: datetime | None = None
    updated_at: datetime


class UsageTrendPoint(BaseModel):
    """One month of data in a workspace usage trend series."""
    year: int
    month: int
    period: str           # "YYYY-MM"
    total_cost_usd: float
    total_requests: int
    blocked_requests: int
    total_tokens: int


# ── Budget status & alerts ────────────────────────────────────────────────────

class BudgetAlertRead(BaseModel):
    threshold_pct: int           # 50 | 80 | 95 | 100
    triggered_at: datetime
    spend_at_trigger: float
    budget_usd: float


class BudgetStatus(BaseModel):
    workspace_id: UUID
    budget_usd: float
    spend_usd: float
    remaining_usd: float
    utilisation_pct: float
    alerts: list[BudgetAlertRead] = Field(default_factory=list)


# ── Spending anomalies ────────────────────────────────────────────────────────

class SpendingAnomalyRead(BaseModel):
    id: UUID
    workspace_id: UUID
    anomaly_type: Literal["spend_spike", "budget_trajectory", "rapid_acceleration"]
    severity: Literal["low", "medium", "high", "critical"]
    current_value: float
    baseline_value: float
    deviation_pct: float
    description: str
    resolved: bool
    resolved_at: datetime | None = None
    detected_at: datetime


# ── Invoice ───────────────────────────────────────────────────────────────────

class MemberUsage(BaseModel):
    user_uuid: UUID
    messages_used: int
    budget_used_usd: float


class InvoiceSummary(BaseModel):
    workspace_id: UUID
    workspace_name: str
    workspace_slug: str
    workspace_plan: str
    period: str               # "YYYY-MM"
    budget_usd: float
    total_cost_usd: float
    total_requests: int
    blocked_requests: int
    total_tokens: int
    last_request_at: datetime | None = None
    budget_utilisation_pct: float
    member_breakdown: list[MemberUsage] = Field(default_factory=list)
    budget_alerts: list[BudgetAlertRead] = Field(default_factory=list)


# ── Admin schemas ─────────────────────────────────────────────────────────────

class WorkspaceWithStats(WorkspaceRead):
    """Workspace row enriched with current-month usage counters."""
    current_month_spend_usd: float  = 0.0
    current_month_requests: int     = 0
    budget_utilisation_pct: float   = 0.0
    active_anomalies: int           = 0
    member_count: int               = 0


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class PlatformStats(BaseModel):
    period: str
    total_workspaces: int
    active_workspaces: int
    suspended_workspaces: int
    total_cost_usd: float
    total_requests: int
    blocked_requests: int
    workspaces_over_80pct: int
    active_anomalies: int


# ── Extended quota responses ──────────────────────────────────────────────────
# CheckUsageResponse and ProxyOpenRouterResponse gain optional workspace fields
# so API consumers can surface workspace budget remaining alongside user quota.

class WorkspaceQuotaContext(BaseModel):
    """Workspace fields injected into quota responses when the user has a workspace."""
    workspace_id: UUID | None          = None
    workspace_budget_usd: float | None = None
    workspace_spend_usd: float         = 0.0
    workspace_remaining_usd: float | None = None
