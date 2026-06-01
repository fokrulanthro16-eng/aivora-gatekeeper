# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install only what pip needs to compile wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Install into an isolated prefix so the final image can COPY cleanly
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: production image ─────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for defence-in-depth
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/

# Compile to bytecode at build time (faster cold start, catches syntax errors)
RUN python -m compileall app/ -q

# Drop to non-root
USER appuser

EXPOSE 8000

# Prometheus scrape port is the same — the /metrics endpoint is on 8000
# Liveness:  GET /health
# Readiness: GET /ready
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# Use 1 worker per container; scale horizontally with replicas.
# Workers > 1 would give each replica its own circuit-breaker + cache state,
# which is acceptable but loses the in-process aggregation benefit.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
