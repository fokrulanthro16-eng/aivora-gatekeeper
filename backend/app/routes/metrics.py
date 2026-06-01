"""
Prometheus metrics scrape endpoint.

  GET /metrics   — text/plain; version=0.0.4 (standard Prometheus exposition format)

This route is excluded from the OpenAPI docs (include_in_schema=False) and from
GatewayMiddleware quota checks (added to GATEWAY_BYPASS_PREFIXES in config).

Scraped by Prometheus at /metrics; compatible with Grafana, Datadog, etc.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["Operations"])


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    include_in_schema=False,
    summary="Prometheus metrics scrape endpoint.",
)
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )
