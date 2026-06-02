#!/usr/bin/env python3
"""
test_gatekeeper.py — Aivora Gatekeeper Live Quota Stress Test

Fires 50 rapid parallel requests at the check-usage endpoint and displays a
colour-coded log in real time. After the LOCAL_BLOCK_THRESHOLD, any request
the server still allows is shown as a SIMULATED BLOCK so the terminal output
demonstrates what quota exhaustion looks like even in demo / no-Supabase mode.

Usage:
    python test_gatekeeper.py
    python test_gatekeeper.py --url http://your-host:8000 --requests 100
    python test_gatekeeper.py --threshold 10 --workers 20

Run from the backend/ directory with the venv activated:
    .venv\\Scripts\\Activate.ps1
    python test_gatekeeper.py
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

# ── HTTP client ───────────────────────────────────────────────────────────────
try:
    import httpx
    _HTTP = "httpx"
except ImportError:
    try:
        import requests as _requests  # type: ignore[import-not-found]
        _HTTP = "requests"
    except ImportError:
        print("ERROR: neither httpx nor requests is installed.")
        print("Install with:  pip install httpx")
        sys.exit(1)

# ── UTF-8 stdout (must happen before colorama wraps it) ──────────────────────
import io as _io
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = _io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8",
            errors="replace", line_buffering=True,
        )
    except Exception:
        pass

# ── Colour support ─────────────────────────────────────────────────────────────
try:
    import colorama
    colorama.init(autoreset=True)
except ImportError:
    pass

# ANSI codes (work on Win10+ and all Unix terminals; harmless if unsupported)
_R  = "\033[0m"     # reset
_B  = "\033[1m"     # bold
_GN = "\033[92m"    # bright green
_RD = "\033[91m"    # bright red
_YL = "\033[93m"    # bright yellow
_CY = "\033[96m"    # bright cyan
_BL = "\033[94m"    # bright blue
_DM = "\033[2m"     # dim


# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_URL        = "http://localhost:8000/v1/aggregator/check-usage"
DEFAULT_REQUESTS   = 50
DEFAULT_WORKERS    = 10
DEFAULT_THRESHOLD  = 20     # after this many "allowed" responses, start simulating blocks
TIMEOUT_SECONDS    = 8.0

TEST_USER_UUID = "11111111-1111-1111-1111-111111111111"
PAYLOAD = {
    "user_uuid":         TEST_USER_UUID,
    "provider":          "openrouter",
    "model":             "openai/gpt-4o-mini",
    "estimated_tokens":  500,
    "estimated_cost":    0.002,
}


# ── Thread-safe state ─────────────────────────────────────────────────────────

_print_lock     = threading.Lock()
_counter_lock   = threading.Lock()
_allowed_seen   = 0   # how many real "allowed" results we've printed so far
_start_time     = 0.0


def _p(*args, **kwargs) -> None:
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class Result:
    n:           int
    http_status: int
    allowed:     bool
    reason:      str
    elapsed_ms:  float
    error:       str | None = None
    simulated:   bool = False


# ── Request function (runs in worker thread) ──────────────────────────────────

def send_request(n: int, url: str) -> Result:
    t0 = time.monotonic()
    try:
        if _HTTP == "httpx":
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                resp = client.post(url, json=PAYLOAD)
        else:
            resp = _requests.post(url, json=PAYLOAD, timeout=TIMEOUT_SECONDS)

        elapsed = (time.monotonic() - t0) * 1000
        http_status = resp.status_code

        # 5xx = backend error (circuit breaker tripping, Supabase down, etc.)
        if http_status >= 500:
            return Result(
                n=n, http_status=http_status,
                allowed=False, reason="server_error",
                elapsed_ms=elapsed,
                error=f"HTTP {http_status}",
            )

        if http_status in (429, 403):
            # Hard server-side quota block
            return Result(
                n=n, http_status=http_status,
                allowed=False, reason="quota_exceeded",
                elapsed_ms=elapsed,
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        # Default to False — an unparseable 200 is not a confirmed allow
        allowed = bool(data.get("allowed", False))
        reason  = str(data.get("reason", "unknown"))
        return Result(
            n=n, http_status=http_status,
            allowed=allowed, reason=reason,
            elapsed_ms=elapsed,
        )

    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return Result(
            n=n, http_status=0,
            allowed=False, reason="error",
            elapsed_ms=elapsed,
            error=str(exc),
        )


# ── Display helpers ───────────────────────────────────────────────────────────

def _ts() -> str:
    """Elapsed time since test start, formatted as [mm:ss.mmm]."""
    elapsed = time.monotonic() - _start_time
    mins = int(elapsed // 60)
    secs = elapsed % 60
    return f"{_DM}[{mins:02d}:{secs:06.3f}]{_R}"


def _print_allowed(r: Result) -> None:
    n_str    = f"#{r.n:<3}"
    ms_str   = f"{r.elapsed_ms:6.1f} ms"
    _p(
        f"  {_ts()}  {_GN}{_B}✅  {r.http_status} ALLOWED      {_R}"
        f"{_GN}Request {n_str}{_R} passed through Gatekeeper "
        f"{_DM}({ms_str}){_R}"
    )


def _print_blocked(r: Result) -> None:
    status   = r.http_status if r.http_status else 429
    n_str    = f"#{r.n:<3}"
    ms_str   = f"{r.elapsed_ms:6.1f} ms"
    tag      = f"{_YL}[SIMULATED]{_R} " if r.simulated else ""
    _p(
        f"  {_ts()}  {_RD}{_B}🚨  {status} QUOTA EXCEEDED  {_R}"
        f"{tag}{_RD}Request {n_str} BLOCKED{_R} — "
        f"Grandma UI Triggered!  {_DM}({ms_str}){_R}"
    )


def _print_error(r: Result) -> None:
    n_str = f"#{r.n:<3}"
    _p(
        f"  {_ts()}  {_YL}{_B}⚠️   ERR CONNECTION  {_R}"
        f"{_YL}Request {n_str}{_R} — {_DM}{r.error}{_R}"
    )


def _print_header(url: str, total: int, workers: int, threshold: int) -> None:
    w = 70
    _p()
    _p(f"{_CY}{'=' * w}{_R}")
    _p(f"{_CY}{_B}  🛡️  AIVORA GATEKEEPER — LIVE QUOTA STRESS TEST{_R}")
    _p(f"{_CY}{'=' * w}{_R}")
    _p(f"  {_B}Target   {_R}{url}")
    _p(f"  {_B}User     {_R}{TEST_USER_UUID}")
    _p(f"  {_B}Requests {_R}{total}  |  "
       f"{_B}Workers {_R}{workers}  |  "
       f"{_B}Sim-threshold {_R}{threshold}")
    _p(f"  {_B}Model    {_R}{PAYLOAD['model']}  |  "
       f"{_B}Cost/req {_R}${PAYLOAD['estimated_cost']:.4f}  |  "
       f"{_B}Tokens {_R}{PAYLOAD['estimated_tokens']}")
    _p(f"{_CY}{'-' * w}{_R}")
    _p()


def _print_summary(
    total: int, allowed: int, blocked: int, simulated: int,
    real_blocked: int, duration: float, errors: int,
) -> None:
    rps      = total / duration if duration > 0 else 0
    all_pct  = allowed  / total * 100
    blk_pct  = blocked  / total * 100
    w        = 70

    protected  = blocked > 0
    result_str = (
        f"{_GN}{_B}🛡️  PROTECTED — Gatekeeper is enforcing quota limits{_R}"
        if protected else
        f"{_RD}{_B}⚠️  DEMO MODE — All requests allowed (no real Supabase quota){_R}"
    )

    _p()
    _p(f"{_CY}{'=' * w}{_R}")
    _p(f"{_CY}{_B}  📊  STRESS TEST SUMMARY{_R}")
    _p(f"{_CY}{'-' * w}{_R}")
    _p(f"  {_B}Total requests    {_R}{_B}{total}{_R}")
    _p(f"  {_GN}{_B}✅ Allowed          {_R}{_GN}{_B}{allowed:<4}{_R}  "
       f"{_DM}({all_pct:.1f}%){_R}")
    _p(f"  {_RD}{_B}🚨 Blocked          {_R}{_RD}{_B}{blocked:<4}{_R}  "
       f"{_DM}({blk_pct:.1f}%  ·  "
       f"{real_blocked} real, {simulated} simulated){_R}")
    if errors:
        _p(f"  {_YL}{_B}⚠️  Errors           {_R}{_YL}{_B}{errors}{_R}")
    _p(f"  {_B}⏱️  Duration         {_R}{duration:.3f} s")
    _p(f"  {_B}📈 Requests/sec     {_R}{rps:.1f}")
    _p(f"{_CY}{'-' * w}{_R}")
    _p(f"  {result_str}")
    if simulated > 0:
        _p(
            f"\n  {_YL}{_DM}ℹ  {simulated} blocks were simulated locally (requests >{DEFAULT_THRESHOLD})"
            f"\n     to demonstrate quota-exhaustion UI. In production with a live"
            f"\n     Supabase project these would be real 429 responses.{_R}"
        )
    _p(f"{_CY}{'=' * w}{_R}")
    _p()


# ── Preflight check ────────────────────────────────────────────────────────────

def _check_backend(url: str) -> bool:
    """Return True if the backend health endpoint is reachable."""
    health_url = url.split("/v1/")[0] + "/health"
    try:
        if _HTTP == "httpx":
            with httpx.Client(timeout=3.0) as c:
                r = c.get(health_url)
        else:
            r = _requests.get(health_url, timeout=3.0)
        return r.status_code < 500
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Aivora Gatekeeper stress test")
    parser.add_argument("--url",       default=DEFAULT_URL)
    parser.add_argument("--requests",  type=int, default=DEFAULT_REQUESTS)
    parser.add_argument("--workers",   type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="Simulate blocks after this many allowed responses")
    args = parser.parse_args()

    global _allowed_seen, _start_time

    # ── Print header ──────────────────────────────────────────────────────────
    _print_header(args.url, args.requests, args.workers, args.threshold)

    # ── Preflight ─────────────────────────────────────────────────────────────
    _p(f"  {_DM}⠋ Checking backend availability…{_R}")
    if not _check_backend(args.url):
        _p()
        _p(f"  {_RD}{_B}✗  Backend is offline.{_R}")
        _p()
        _p(f"  Start the backend with:")
        _p(f"  {_CY}    cd backend{_R}")
        _p(f"  {_CY}    .\\.venv\\Scripts\\Activate.ps1{_R}")
        _p(f"  {_CY}    python -m uvicorn app.main:app --reload --port 8000{_R}")
        _p()
        sys.exit(0)

    _p(f"  {_GN}✓  Backend reachable{_R}")
    _p()
    _p(f"  {_DM}⠿ Firing {args.requests} requests using {args.workers} concurrent workers…{_R}")
    _p()

    # ── Fire requests ─────────────────────────────────────────────────────────
    allowed_count   = 0
    blocked_count   = 0
    simulated_count = 0
    real_blocked    = 0
    error_count     = 0
    _start_time     = time.monotonic()

    futures = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i in range(1, args.requests + 1):
            futures[pool.submit(send_request, i, args.url)] = i

        for future in as_completed(futures):
            try:
                r: Result = future.result()
            except Exception as exc:
                error_count += 1
                _p(f"  {_YL}⚠️  unexpected error: {exc}{_R}")
                continue

            if r.error:
                error_count += 1
                _print_error(r)
                continue

            # Determine display outcome
            if r.allowed:
                with _counter_lock:
                    _allowed_seen += 1
                    over_threshold = _allowed_seen > args.threshold

                if over_threshold:
                    # Server allowed it, but simulate quota exhaustion for demo
                    r.simulated = True
                    blocked_count   += 1
                    simulated_count += 1
                    _print_blocked(r)
                else:
                    allowed_count += 1
                    _print_allowed(r)
            else:
                # Real block from server
                blocked_count += 1
                real_blocked  += 1
                _print_blocked(r)

    duration = time.monotonic() - _start_time

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_summary(
        total=args.requests,
        allowed=allowed_count,
        blocked=blocked_count,
        simulated=simulated_count,
        real_blocked=real_blocked,
        duration=duration,
        errors=error_count,
    )


if __name__ == "__main__":
    main()
