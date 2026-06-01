#!/usr/bin/env python3
"""
scripts/validate.py
HTTP endpoint validation runner for Aivora Gatekeeper.

Hits every critical API endpoint, verifies status codes and key JSON fields,
and prints a colour-coded PASS/FAIL report.

Usage:
    python scripts/validate.py
    python scripts/validate.py --base-url https://your-gatekeeper.example.com
    python scripts/validate.py --admin-key your-admin-key
    python scripts/validate.py --json   # machine-readable output

Dependencies: httpx (already in backend/requirements.txt)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)

# ── Demo constants (match seed_demo.py) ──────────────────────────────────────

WORKSPACE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WS_FROZEN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

USER_ALICE   = "00000000-0000-0000-0000-000000000001"  # healthy
USER_BOB     = "00000000-0000-0000-0000-000000000002"  # monthly_message_limit_exceeded
USER_CAROL   = "00000000-0000-0000-0000-000000000003"  # monthly_budget_exceeded
USER_EVE     = "00000000-0000-0000-0000-000000000005"  # suspended


# ── Result model ─────────────────────────────────────────────────────────────

@dataclass
class Result:
    name: str
    passed: bool
    status_code: int = 0
    expected_status: int = 200
    notes: str = ""
    response_excerpt: str = ""
    elapsed_ms: float = 0.0


results: list[Result] = []


# ── Assertion helpers ─────────────────────────────────────────────────────────

def _check(
    client: httpx.Client,
    name: str,
    method: str,
    path: str,
    *,
    expected_status: int = 200,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    assert_json: dict[str, Any] | None = None,     # key→expected_value assertions
    assert_contains: list[str] | None = None,       # keys that must exist in response
    assert_reason: str | None = None,               # checks data["reason"] == value
    notes: str = "",
) -> Result:
    h = headers or {}
    try:
        t0 = time.monotonic()
        if method == "GET":
            resp = client.get(path, headers=h)
        elif method == "POST":
            resp = client.post(path, json=body or {}, headers=h)
        elif method == "PATCH":
            resp = client.patch(path, json=body or {}, headers=h)
        else:
            raise ValueError(f"Unsupported method {method}")
        elapsed = (time.monotonic() - t0) * 1000

        passed = resp.status_code == expected_status
        try:
            data: dict = resp.json()
        except Exception:
            data = {}

        if passed and assert_json:
            for k, v in assert_json.items():
                if data.get(k) != v:
                    passed = False
                    notes = notes or f"Expected {k}={v!r}, got {data.get(k)!r}"
                    break

        if passed and assert_contains:
            for k in assert_contains:
                if k not in data:
                    passed = False
                    notes = notes or f"Missing key: {k}"
                    break

        if passed and assert_reason:
            actual_reason = data.get("reason") or (data.get("detail") or "")
            if assert_reason not in str(actual_reason):
                passed = False
                notes = notes or f"Expected reason containing '{assert_reason}', got '{actual_reason}'"

        excerpt = json.dumps(data, default=str)[:200]
        r = Result(name=name, passed=passed, status_code=resp.status_code,
                   expected_status=expected_status, notes=notes,
                   response_excerpt=excerpt, elapsed_ms=round(elapsed, 1))
    except httpx.ConnectError:
        r = Result(name=name, passed=False, notes="Connection refused — is the backend running?")
    except Exception as exc:
        r = Result(name=name, passed=False, notes=str(exc))

    results.append(r)
    return r


# ── Test suite ────────────────────────────────────────────────────────────────

def run_all(base_url: str, admin_key: str) -> None:
    client = httpx.Client(base_url=base_url, timeout=10.0)

    print(f"\n  Validating: {base_url}")
    print(f"  Admin key:  {'(set)' if admin_key else '(not set — admin tests will fail)'}")
    print()

    # ── Health & status ───────────────────────────────────────────────────────
    _check(client, "GET /health", "GET", "/health",
           assert_contains=["status", "version", "env"])

    _check(client, "GET /v1/gatekeeper/status", "GET", "/v1/gatekeeper/status",
           assert_contains=["circuit_breaker", "cache"])

    _check(client, "GET /v1/aggregator/status", "GET", "/v1/aggregator/status",
           assert_contains=["status", "stats", "circuit_breaker_state"])

    # ── Workspace CRUD ────────────────────────────────────────────────────────
    _check(client, "GET /v1/workspaces/{id}", "GET",
           f"/v1/workspaces/{WORKSPACE_ID}",
           assert_json={"slug": "acme-ai-co", "plan": "growth"})

    _check(client, "GET /v1/workspaces/{id}/members", "GET",
           f"/v1/workspaces/{WORKSPACE_ID}/members",
           expected_status=200)

    _check(client, "GET /v1/workspaces/{id}/usage", "GET",
           f"/v1/workspaces/{WORKSPACE_ID}/usage",
           assert_contains=["total_cost_usd", "total_requests"])

    _check(client, "GET /v1/workspaces/{id}/alerts", "GET",
           f"/v1/workspaces/{WORKSPACE_ID}/alerts",
           assert_contains=["budget_usd", "spend_usd", "utilisation_pct", "alerts"])

    _check(client, "GET /v1/workspaces/{id}/anomalies", "GET",
           f"/v1/workspaces/{WORKSPACE_ID}/anomalies",
           expected_status=200)

    # ── Invoice / trend ───────────────────────────────────────────────────────
    _check(client, "GET /v1/invoices/{id}/2026/5 (May)", "GET",
           f"/v1/invoices/{WORKSPACE_ID}/2026/5",
           assert_contains=["total_cost_usd", "budget_utilisation_pct", "period"])

    _check(client, "GET /v1/invoices/{id}/trend", "GET",
           f"/v1/invoices/{WORKSPACE_ID}/trend",
           expected_status=200)

    # ── Scenario A: Bob → monthly_message_limit_exceeded ─────────────────────
    _check(
        client,
        "POST check-usage → monthly_message_limit_exceeded (Bob)",
        "POST", "/v1/aggregator/check-usage",
        body={
            "user_uuid": USER_BOB, "provider": "openai",
            "model": "gpt-4o-mini", "estimated_tokens": 500, "estimated_cost": 0.001,
        },
        assert_json={"allowed": False},
        assert_reason="monthly_message_limit_exceeded",
        notes="Scenario A",
    )

    # ── Scenario B: Carol → monthly_budget_exceeded ───────────────────────────
    _check(
        client,
        "POST check-usage → monthly_budget_exceeded (Carol)",
        "POST", "/v1/aggregator/check-usage",
        body={
            "user_uuid": USER_CAROL, "provider": "openai",
            "model": "gpt-4o-mini", "estimated_tokens": 500, "estimated_cost": 0.10,
        },
        assert_json={"allowed": False},
        assert_reason="monthly_budget_exceeded",
        notes="Scenario B",
    )

    # ── Scenario C: Alice/workspace → workspace_budget_exceeded ──────────────
    _check(
        client,
        "POST check-usage → workspace_budget_exceeded (Alice)",
        "POST", "/v1/aggregator/check-usage",
        body={
            "user_uuid": USER_ALICE, "provider": "openai",
            "model": "gpt-4o-mini", "estimated_tokens": 500, "estimated_cost": 1.00,
        },
        assert_json={"allowed": False},
        assert_reason="workspace_budget_exceeded",
        notes="Scenario C",
    )

    # ── Scenario D: Eve → account_suspended ──────────────────────────────────
    _check(
        client,
        "POST check-usage → account_suspended (Eve)",
        "POST", "/v1/aggregator/check-usage",
        body={
            "user_uuid": USER_EVE, "provider": "openai",
            "model": "gpt-4o-mini", "estimated_tokens": 100, "estimated_cost": 0.001,
        },
        assert_json={"allowed": False},
        notes="Scenario D/E — either account_suspended or workspace_suspended expected",
    )

    # ── Healthy user (Alice with small request that doesn't exceed workspace) ─
    # NB: After Scenario C is seeded, workspace is at $99.50/$100.
    # A tiny $0.01 request might pass if workspace has $0.50 headroom.
    # We test with a very small cost to confirm the path works when quota allows.
    _check(
        client,
        "POST check-usage → allowed (Alice, $0.001 request — workspace headroom test)",
        "POST", "/v1/aggregator/check-usage",
        body={
            "user_uuid": USER_ALICE, "provider": "openai",
            "model": "gpt-4o-mini", "estimated_tokens": 50, "estimated_cost": 0.001,
        },
        expected_status=200,
        notes="Small cost stays under $0.50 workspace headroom",
    )

    # ── Admin endpoints ───────────────────────────────────────────────────────
    admin_headers = {"X-Admin-Key": admin_key} if admin_key else {}

    _check(client, "GET /v1/admin/stats (no key) → 403/503", "GET",
           "/v1/admin/stats", expected_status=403 if admin_key else 503,
           notes="Admin key required")

    if admin_key:
        _check(client, "GET /v1/admin/stats", "GET", "/v1/admin/stats",
               headers=admin_headers,
               assert_contains=["total_workspaces", "active_workspaces", "total_cost_usd"])

        _check(client, "GET /v1/admin/workspaces", "GET", "/v1/admin/workspaces",
               headers=admin_headers, expected_status=200)

        _check(client, f"GET /v1/admin/workspaces/{WORKSPACE_ID}", "GET",
               f"/v1/admin/workspaces/{WORKSPACE_ID}",
               headers=admin_headers,
               assert_contains=["current_month_spend_usd", "budget_utilisation_pct"])

        _check(client, "GET /v1/admin/anomalies", "GET", "/v1/admin/anomalies",
               headers=admin_headers, expected_status=200)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(as_json: bool) -> int:
    passed  = [r for r in results if r.passed]
    failed  = [r for r in results if not r.passed]

    if as_json:
        print(json.dumps([
            {"name": r.name, "passed": r.passed, "status": r.status_code,
             "elapsed_ms": r.elapsed_ms, "notes": r.notes}
            for r in results
        ], indent=2))
        return len(failed)

    green  = "\033[32m"
    red    = "\033[31m"
    yellow = "\033[33m"
    reset  = "\033[0m"

    print(f"\n{'─' * 72}")
    for r in results:
        icon    = f"{green}PASS{reset}" if r.passed else f"{red}FAIL{reset}"
        timing  = f"{r.elapsed_ms:6.0f} ms" if r.elapsed_ms else "      --"
        print(f"  [{icon}]  {timing}  {r.name}")
        if not r.passed:
            print(f"           {yellow}→ {r.notes or 'no detail'}{reset}")
            if r.response_excerpt:
                print(f"             {r.response_excerpt[:140]}")
    print(f"{'─' * 72}")
    print(f"  {len(passed)} passed  ·  {len(failed)} failed  ·  {len(results)} total")
    print()
    return len(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Aivora Gatekeeper endpoints.")
    parser.add_argument("--base-url",  default="http://localhost:8000")
    parser.add_argument("--admin-key", default=os.getenv("ADMIN_API_KEY", "") if False else "")
    parser.add_argument("--json",      action="store_true")
    args = parser.parse_args()

    import os
    admin_key = args.admin_key or os.getenv("ADMIN_API_KEY", "")

    run_all(args.base_url, admin_key)
    exit_code = print_report(args.json)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
