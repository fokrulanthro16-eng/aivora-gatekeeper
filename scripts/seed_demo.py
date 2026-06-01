#!/usr/bin/env python3
"""
scripts/seed_demo.py
Full automated demo-data seed for Aivora Gatekeeper.

Creates auth users via the Supabase Admin API, provisions quotas, builds the
demo workspace, populates 3 months of usage history, forces scenario states,
and inserts budget alerts + anomalies.

Usage:
    cd backend
    python ../scripts/seed_demo.py

    # Dry-run (print what would be created, no DB writes):
    python ../scripts/seed_demo.py --dry-run

    # Skip auth user creation (users already exist in your Supabase project):
    python ../scripts/seed_demo.py --skip-auth

Requirements: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in
backend/.env or as environment variables.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Allow running from the repo root or the scripts/ directory ────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass  # python-dotenv not installed — rely on shell environment

# ── Demo UUIDs (fixed — match SQL seeds exactly) ──────────────────────────────

WORKSPACE_ID       = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WS_FROZEN_ID       = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

USER_ALICE         = "00000000-0000-0000-0000-000000000001"  # Pro, workspace owner
USER_BOB           = "00000000-0000-0000-0000-000000000002"  # Free, quota exhausted
USER_CAROL         = "00000000-0000-0000-0000-000000000003"  # Pro, budget exhausted
USER_DAVE          = "00000000-0000-0000-0000-000000000004"  # Enterprise, healthy
USER_EVE           = "00000000-0000-0000-0000-000000000005"  # Pro, suspended

DEMO_PASSWORD      = "Demo1234!"
DEMO_USERS = [
    {"id": USER_ALICE, "email": "alice@demo.aivora.ai", "name": "Alice Demo", "tier": 2},
    {"id": USER_BOB,   "email": "bob@demo.aivora.ai",   "name": "Bob Demo",   "tier": 1},
    {"id": USER_CAROL, "email": "carol@demo.aivora.ai", "name": "Carol Demo", "tier": 2},
    {"id": USER_DAVE,  "email": "dave@demo.aivora.ai",  "name": "Dave Demo",  "tier": 3},
    {"id": USER_EVE,   "email": "eve@demo.aivora.ai",   "name": "Eve Demo",   "tier": 2},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _skip(msg: str) -> None:
    print(f"  –  {msg} (skipped)")


def _fail(msg: str) -> None:
    print(f"  ✗  {msg}", file=sys.stderr)


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── Main seed logic ───────────────────────────────────────────────────────────

async def seed(dry_run: bool, skip_auth: bool) -> None:
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not key:
        _fail("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)

    if dry_run:
        print("\n[DRY RUN — no database writes will be made]\n")

    # Import here so the script can be imported for testing
    from supabase import acreate_client, AsyncClient

    client: AsyncClient = await acreate_client(url, key)
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    # ── Step 1: Create auth users ─────────────────────────────────────────────
    _section("1 / 7  Auth users")

    if skip_auth:
        _skip("Skipping auth user creation (--skip-auth)")
    else:
        for u in DEMO_USERS:
            if dry_run:
                _skip(f"Would create user {u['email']} id={u['id']}")
                continue
            try:
                await client.auth.admin.create_user({
                    "email":          u["email"],
                    "password":       DEMO_PASSWORD,
                    "email_confirm":  True,
                    "user_metadata":  {"name": u["name"]},
                })
                _ok(f"Created {u['email']}")
            except Exception as exc:
                err = str(exc)
                if "already been registered" in err or "already exists" in err or "duplicate" in err.lower():
                    _skip(f"{u['email']} already exists")
                else:
                    _fail(f"Could not create {u['email']}: {err}")

    # ── Step 2: Provision user_quotas ─────────────────────────────────────────
    _section("2 / 7  Provision user quotas")

    for u in DEMO_USERS:
        if dry_run:
            _skip(f"Would provision quota for {u['id'][:8]}… tier={u['tier']}")
            continue
        try:
            await client.rpc(
                "provision_user_quota",
                {"p_user_uuid": u["id"], "p_billing_tier_id": u["tier"]},
            ).execute()
            _ok(f"Provisioned quota for {u['email']} (tier {u['tier']})")
        except Exception as exc:
            _fail(f"provision_user_quota failed for {u['email']}: {exc}")

    # Suspend Eve's account
    if not dry_run:
        try:
            await client.table("user_quotas").update({
                "is_suspended":      True,
                "suspension_reason": "Policy violation: automated abuse detection triggered",
            }).eq("user_uuid", USER_EVE).execute()
            _ok("Suspended Eve's account")
        except Exception as exc:
            _fail(f"Could not suspend Eve: {exc}")
    else:
        _skip("Would suspend Eve's account")

    # ── Step 3: Create workspaces ─────────────────────────────────────────────
    _section("3 / 7  Workspaces")

    workspaces: list[dict[str, Any]] = [
        {
            "id": WORKSPACE_ID, "name": "Acme AI Co", "slug": "acme-ai-co",
            "owner_uuid": USER_ALICE, "plan": "growth",
            "monthly_budget_usd": 100.00, "is_active": True, "is_suspended": False,
        },
        {
            "id": WS_FROZEN_ID, "name": "Frozen Corp", "slug": "frozen-corp",
            "owner_uuid": USER_EVE, "plan": "starter",
            "monthly_budget_usd": 50.00, "is_active": True, "is_suspended": True,
            "suspension_reason": "Fraudulent activity detected — account under review. Ref: FRAUD-2026-0601",
        },
    ]

    for ws in workspaces:
        if dry_run:
            _skip(f"Would upsert workspace '{ws['name']}' id={ws['id'][:8]}…")
            continue
        try:
            await client.table("workspaces").upsert(ws, on_conflict="id").execute()
            _ok(f"Upserted workspace '{ws['name']}'")
        except Exception as exc:
            _fail(f"Could not upsert workspace '{ws['name']}': {exc}")

    # Members
    members: list[dict[str, Any]] = [
        {"workspace_id": WORKSPACE_ID, "user_uuid": USER_ALICE, "role": "owner"},
        {"workspace_id": WORKSPACE_ID, "user_uuid": USER_DAVE,  "role": "admin"},
        {"workspace_id": WORKSPACE_ID, "user_uuid": USER_CAROL, "role": "member"},
        {"workspace_id": WS_FROZEN_ID, "user_uuid": USER_EVE,   "role": "owner"},
    ]
    if not dry_run:
        try:
            await client.table("workspace_members").upsert(
                members, on_conflict="workspace_id,user_uuid"
            ).execute()
            _ok("Upserted workspace members")
        except Exception as exc:
            _fail(f"Could not upsert members: {exc}")

        # Link workspace_id on quota rows
        for uid, wid in [
            (USER_ALICE, WORKSPACE_ID),
            (USER_DAVE,  WORKSPACE_ID),
            (USER_CAROL, WORKSPACE_ID),
            (USER_EVE,   WS_FROZEN_ID),
        ]:
            try:
                await client.table("user_quotas").update(
                    {"workspace_id": wid}
                ).eq("user_uuid", uid).execute()
            except Exception as exc:
                _fail(f"Could not link {uid[:8]}… to workspace: {exc}")
        _ok("Linked user_quotas.workspace_id")

    # ── Step 4: Usage history ─────────────────────────────────────────────────
    _section("4 / 7  Usage history (3 months + current)")

    workspace_usage: list[dict[str, Any]] = [
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 3,
         "total_requests": 213, "blocked_requests": 4,
         "total_tokens": 1_065_000, "total_cost_usd": 19.87},
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 4,
         "total_requests": 287, "blocked_requests": 6,
         "total_tokens": 1_435_000, "total_cost_usd": 24.53},
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 5,
         "total_requests": 341, "blocked_requests": 11,
         "total_tokens": 1_705_000, "total_cost_usd": 22.36},
        {"workspace_id": WORKSPACE_ID, "year": year,  "month": month,
         "total_requests": 47, "blocked_requests": 2,
         "total_tokens": 235_000, "total_cost_usd": 4.12},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 4,
         "total_requests": 1204, "blocked_requests": 89,
         "total_tokens": 6_020_000, "total_cost_usd": 48.92},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 5,
         "total_requests": 2847, "blocked_requests": 312,
         "total_tokens": 14_235_000, "total_cost_usd": 117.43},
    ]

    if not dry_run:
        try:
            await client.table("workspace_monthly_usage").upsert(
                workspace_usage, on_conflict="workspace_id,year,month"
            ).execute()
            _ok(f"Upserted {len(workspace_usage)} workspace_monthly_usage rows")
        except Exception as exc:
            _fail(f"Could not insert workspace usage: {exc}")
    else:
        _skip(f"Would insert {len(workspace_usage)} workspace_monthly_usage rows")

    # Per-user usage_counters (current month)
    period_start = datetime(year, month, 1, tzinfo=timezone.utc).isoformat()
    user_counters: list[dict[str, Any]] = [
        {"user_uuid": USER_ALICE, "period_start": period_start, "messages_used": 47,  "budget_used_usd": 2.18},
        {"user_uuid": USER_BOB,   "period_start": period_start, "messages_used": 50,  "budget_used_usd": 0.47},   # Scenario A
        {"user_uuid": USER_CAROL, "period_start": period_start, "messages_used": 892, "budget_used_usd": 19.97},  # Scenario B
        {"user_uuid": USER_DAVE,  "period_start": period_start, "messages_used": 412, "budget_used_usd": 31.87},
    ]

    if not dry_run:
        try:
            await client.table("usage_counters").upsert(
                user_counters, on_conflict="user_uuid,period_start"
            ).execute()
            _ok(f"Upserted {len(user_counters)} usage_counter rows")
        except Exception as exc:
            _fail(f"Could not insert usage_counters: {exc}")
    else:
        _skip(f"Would insert {len(user_counters)} usage_counter rows")

    # ── Step 5: Scenario states ───────────────────────────────────────────────
    _section("5 / 7  Scenario states")

    # Scenario C: push workspace total_cost_usd to $99.50 (near $100 cap)
    if not dry_run:
        try:
            await client.table("workspace_monthly_usage").update({
                "total_cost_usd":   99.50,
                "total_requests":   952,
                "blocked_requests": 14,
            }).eq("workspace_id", WORKSPACE_ID).eq("year", year).eq("month", month).execute()
            _ok("Set Acme AI Co workspace spend to $99.50 (Scenario C: workspace_budget_exceeded)")
        except Exception as exc:
            _fail(f"Could not set workspace scenario: {exc}")
    else:
        _skip("Would set workspace spend to $99.50")

    _ok("Scenario A ready: Bob (…0002) → monthly_message_limit_exceeded")
    _ok("Scenario B ready: Carol (…0003) → monthly_budget_exceeded")
    _ok("Scenario C ready: Alice/Dave (…0001/0004) → workspace_budget_exceeded")
    _ok("Scenario D ready: Eve (…0005) → account_suspended")
    _ok("Scenario E ready: Eve (…0005) → workspace_suspended (Frozen Corp)")

    # ── Step 6: Budget alerts ─────────────────────────────────────────────────
    _section("6 / 7  Budget alerts")

    alerts: list[dict[str, Any]] = [
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 4, "threshold_pct": 50, "spend_at_trigger": 51.23, "budget_usd": 100.00},
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 5, "threshold_pct": 50, "spend_at_trigger": 50.84, "budget_usd": 100.00},
        {"workspace_id": WORKSPACE_ID, "year": 2026, "month": 5, "threshold_pct": 80, "spend_at_trigger": 80.12, "budget_usd": 100.00},
        {"workspace_id": WORKSPACE_ID, "year": year,  "month": month, "threshold_pct": 50, "spend_at_trigger": 50.31, "budget_usd": 100.00},
        {"workspace_id": WORKSPACE_ID, "year": year,  "month": month, "threshold_pct": 80, "spend_at_trigger": 80.77, "budget_usd": 100.00},
        {"workspace_id": WORKSPACE_ID, "year": year,  "month": month, "threshold_pct": 95, "spend_at_trigger": 95.14, "budget_usd": 100.00},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 5, "threshold_pct": 50,  "spend_at_trigger": 25.43, "budget_usd": 50.00},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 5, "threshold_pct": 80,  "spend_at_trigger": 40.22, "budget_usd": 50.00},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 5, "threshold_pct": 95,  "spend_at_trigger": 47.91, "budget_usd": 50.00},
        {"workspace_id": WS_FROZEN_ID, "year": 2026, "month": 5, "threshold_pct": 100, "spend_at_trigger": 50.01, "budget_usd": 50.00},
    ]

    if not dry_run:
        try:
            await client.table("workspace_budget_alerts").upsert(
                alerts, on_conflict="workspace_id,year,month,threshold_pct"
            ).execute()
            _ok(f"Inserted {len(alerts)} budget alert rows")
        except Exception as exc:
            _fail(f"Could not insert budget alerts: {exc}")
    else:
        _skip(f"Would insert {len(alerts)} budget alert rows")

    # ── Step 7: Spending anomalies ────────────────────────────────────────────
    _section("7 / 7  Spending anomalies")

    anomalies: list[dict[str, Any]] = [
        {
            "workspace_id": WORKSPACE_ID,
            "anomaly_type":  "spend_spike",
            "severity":      "high",
            "current_value": 4.12,
            "baseline_value": 0.75,
            "deviation_pct": 449.33,
            "description":   (
                "Daily spend rate $4.12/day is 449% above the 3-month average ($0.75/day). "
                "Possible causes: new high-cost model deployed, runaway automation, or credential leak."
            ),
            "resolved": False,
        },
        {
            "workspace_id": WORKSPACE_ID,
            "anomaly_type":  "budget_trajectory",
            "severity":      "high",
            "current_value": 123.60,
            "baseline_value": 100.00,
            "deviation_pct": 23.60,
            "description":   (
                "At current rate ($4.12/day), projected month-end spend is $123.60 — "
                "123.6% of the $100.00 budget."
            ),
            "resolved": False,
        },
    ]

    if not dry_run:
        try:
            await client.table("spending_anomalies").insert(anomalies).execute()
            _ok(f"Inserted {len(anomalies)} spending anomaly rows")
        except Exception as exc:
            # Duplicate rows on re-run are acceptable
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                _skip("Anomaly rows already exist (re-run detected)")
            else:
                _fail(f"Could not insert anomalies: {exc}")
    else:
        _skip(f"Would insert {len(anomalies)} anomaly rows")

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("Seed complete")
    print("""
  Demo credentials
  ────────────────────────────────────────────────────────
  User    UUID suffix  Email                  Scenario
  Alice   …0001        alice@demo.aivora.ai   Healthy (workspace member)
  Bob     …0002        bob@demo.aivora.ai     monthly_message_limit_exceeded
  Carol   …0003        carol@demo.aivora.ai   monthly_budget_exceeded
  Dave    …0004        dave@demo.aivora.ai    Healthy (workspace admin)
  Eve     …0005        eve@demo.aivora.ai     account_suspended + workspace_suspended
  Password for all:  Demo1234!

  Workspace
  ────────────────────────────────────────────────────────
  Acme AI Co    aaaaaaaa-…  growth  $100/mo  spend=$99.50 (near limit)
  Frozen Corp   bbbbbbbb-…  starter $50/mo   SUSPENDED

  Admin API key:  set ADMIN_API_KEY in your .env, then use:
    curl -H "X-Admin-Key: <key>" http://localhost:8000/v1/admin/stats
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Aivora Gatekeeper demo data.")
    parser.add_argument("--dry-run",    action="store_true", help="Print actions without writing")
    parser.add_argument("--skip-auth",  action="store_true", help="Skip auth user creation")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run, skip_auth=args.skip_auth))


if __name__ == "__main__":
    main()
