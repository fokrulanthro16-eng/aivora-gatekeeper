"""
Aivora Gatekeeper — FastAPI application entry point.

Startup sequence
----------------
1. Configure structlog for JSON-structured logging (plain key=value in dev).
2. Initialise the async Supabase client.
3. Mount CORS middleware.
4. Mount GatewayMiddleware (quota enforcement).
5. Register all routers.

Shutdown sequence
-----------------
1. Flush the in-memory cache (best-effort).
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.middleware.gateway import GatewayMiddleware
from app.routes.admin import router as admin_router
from app.routes.aggregator import router as aggregator_router
from app.routes.gatekeeper import router as gatekeeper_router
from app.routes.invoice import router as invoice_router
from app.routes.workspace import router as workspace_router
from app.services.cache import get_quota_cache
from app.services.supabase_client import init_supabase_client

# ── Structured logging setup ──────────────────────────────────────────────────

def _configure_logging(settings_obj: object) -> None:
    """
    Configure structlog to emit:
      - JSON to stdout in staging / production.
      - Human-readable key=value lines in development (easier to read locally).
    """
    env = getattr(settings_obj, "ENV", "development")
    log_level_name = getattr(settings_obj, "LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if env == "development":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    _configure_logging(settings)

    log = structlog.get_logger(__name__)
    log.info("startup", app=settings.APP_NAME, version=settings.APP_VERSION, env=settings.ENV)

    await init_supabase_client()

    yield

    log.info("shutdown", app=settings.APP_NAME)
    await get_quota_cache().flush()


# ── Application factory ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Token-bucket rate-limiting gateway for AI API traffic. "
            "Enforces per-user quotas via Supabase with Redis-style in-memory "
            "caching and a circuit breaker for resilience."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS ───────────────────────────────────────────────────────────────────
    origins = settings.cors_origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-RateLimit-Remaining", "X-RateLimit-Cost", "X-Gatekeeper-Degraded"],
    )

    # ── Gateway quota middleware ───────────────────────────────────────────────
    # Added AFTER CORSMiddleware so OPTIONS pre-flight requests are not quota-checked.
    app.add_middleware(GatewayMiddleware)

    # ── Routes ─────────────────────────────────────────────────────────────────
    app.include_router(gatekeeper_router)
    app.include_router(aggregator_router)
    app.include_router(workspace_router)
    app.include_router(admin_router)
    app.include_router(invoice_router)

    # ── Global exception handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log = structlog.get_logger(__name__)
        log.error(
            "unhandled_exception",
            path=str(request.url),
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": None,
                }
            },
        )

    return app


app = create_app()
