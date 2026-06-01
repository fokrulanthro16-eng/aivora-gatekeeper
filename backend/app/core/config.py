from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Application ────────────────────────────────────────────────────────────
    APP_NAME: str = "Aivora Gatekeeper"
    APP_VERSION: str = "1.0.0"
    ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Supabase ───────────────────────────────────────────────────────────────
    SUPABASE_URL: str = "http://127.0.0.1:54321"
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_RPC_TIMEOUT_SECONDS: float = Field(default=5.0, ge=0.5, le=30.0)

    # ── OpenRouter ─────────────────────────────────────────────────────────────
    # Required for /v1/aggregator/proxy-openrouter.
    # Obtain from https://openrouter.ai/keys
    OPENROUTER_API_KEY: str = ""

    # ── Polar.sh ───────────────────────────────────────────────────────────────
    # Required for /v1/webhooks/polar signature verification.
    # Obtain from Polar dashboard → Webhooks.
    POLAR_WEBHOOK_SECRET: str = ""
    # Service token for outbound Polar API calls (optional, for subscription lookup).
    POLAR_ACCESS_TOKEN: str = ""

    # ── CORS — comma-separated origins, or "*" for all ─────────────────────────
    CORS_ORIGINS: str = "*"
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # ── In-memory quota cache ──────────────────────────────────────────────────
    # Short TTL for positive (allowed) decisions — keeps deduction errors small.
    CACHE_DEFAULT_TTL_SECONDS: int = Field(default=5, ge=1, le=300)
    # Longer TTL for negative (blocked) decisions — avoid hammering Supabase.
    CACHE_NEGATIVE_TTL_SECONDS: int = Field(default=15, ge=1, le=300)
    CACHE_MAX_ENTRIES: int = Field(default=10_000, ge=100)

    # ── Circuit breaker ────────────────────────────────────────────────────────
    CB_FAILURE_THRESHOLD: int = Field(default=5, ge=1, le=100)
    CB_RECOVERY_TIMEOUT_SECONDS: float = Field(default=60.0, ge=5.0, le=3600.0)
    CB_HALF_OPEN_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=20)

    # ── Token estimation ───────────────────────────────────────────────────────
    TOKEN_CHARS_PER_TOKEN: float = Field(default=4.0, ge=1.0, le=10.0)
    TOKEN_DEFAULT_COST: int = Field(default=10, ge=1)
    TOKEN_MAX_COST: int = Field(default=10_000, ge=10)

    # ── Demo mode ──────────────────────────────────────────────────────────────
    # When True: missing Supabase credentials degrade gracefully (fail-open).
    # When False (default/production): missing credentials reject requests.
    # Set DEMO_MODE=true only for local development without a Supabase instance.
    DEMO_MODE: bool = False

    # ── Authentication ─────────────────────────────────────────────────────────
    # JWT secret from Supabase project Settings → API → JWT Settings.
    # Required for workspace and admin routes to verify user identity.
    # If empty those routes return 503.
    SUPABASE_JWT_SECRET: str = ""

    # ── Spending anomaly detection ─────────────────────────────────────────────
    # Daily burn rate must exceed baseline × this multiplier to trigger a spike.
    ANOMALY_SPIKE_MULTIPLIER: float = Field(default=3.0, ge=1.5, le=100.0)
    # Project month-end spend must exceed budget × this % to trigger a trajectory alert.
    ANOMALY_TRAJECTORY_PCT: float = Field(default=120.0, ge=101.0, le=1000.0)
    # Minimum daily spend (USD) required before anomaly fires — avoids noise.
    ANOMALY_MIN_DAILY_SPEND_USD: float = Field(default=0.10, ge=0.0, le=100.0)

    # ── Gateway behaviour ──────────────────────────────────────────────────────
    # Comma-separated exact paths that bypass quota checking.
    GATEWAY_BYPASS_PATHS: str = (
        "/health,/docs,/openapi.json,/redoc,"
        "/v1/gatekeeper/status,/v1/gatekeeper/protect,"
        "/v1/gatekeeper/simulate-request,"
        "/v1/aggregator/status,/v1/aggregator/check-usage,"
        "/v1/aggregator/proxy-openrouter,/v1/webhooks/polar"
    )
    # Comma-separated path PREFIXES that bypass quota checking.
    # Dynamic route segments (/{id}/...) are covered by prefix matching.
    GATEWAY_BYPASS_PREFIXES: str = "/v1/workspaces,/v1/admin,/v1/invoices,/metrics,/ready"
    # When True the gate fails open (allows) if Supabase is unreachable.
    # Production default is False (fail-closed). Only set True in demo mode
    # or if you explicitly accept the over-spend risk during outages.
    GATEWAY_FAIL_OPEN: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def bypass_paths_set(self) -> frozenset[str]:
        return frozenset(
            p.strip() for p in self.GATEWAY_BYPASS_PATHS.split(",") if p.strip()
        )

    @property
    def bypass_prefixes_list(self) -> list[str]:
        return [p.strip() for p in self.GATEWAY_BYPASS_PREFIXES.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
