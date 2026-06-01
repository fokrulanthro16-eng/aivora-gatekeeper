#!/usr/bin/env python3
"""
scripts/reset_demo.py
Reset all demo data to a clean state, then optionally re-seed.

Removes:
  • All workspace_monthly_usage rows for demo workspaces
  • All usage_counters rows for demo users (current month)
  • All spending_anomalies rows for demo workspaces
  • All workspace_budget_alerts rows for demo workspaces
  • Restores user_quotas bucket levels to full
  • Unsuspends demo accounts / workspaces

Does NOT delete:
  • auth.users rows (use Supabase dashboard to delete users)
  • Historical usage_counters rows (previous months)
  • workspace members

Usage:
    python scripts/reset_demo.py
    python scripts/reset_demo.py --and-reseed   # reset then run seed_demo.py
    python scripts/reset_demo.py --full          # also delete workspaces + quotas
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

WORKSPACE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WS_FROZEN_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

USER_ALICE = "00000000-0000-0000-0000-000000000001"
USER_BOB   = "00000000-0000-0000-0000-000000000002"
USER_CAROL = "00000000-0000-0000-0000-000000000003"
USER_DAVE  = "00000000-0000-0000-0000-000000000004"
USER_EVE   = "00000000-0000-0000-0000-000000000005"
ALL_USERS  = [USER_ALICE, USER_BOB, USER_CAROL, USER_DAVE, USER_EVE]


def _ok(msg: str) -> None:  print(f"  ✓  {msg}")
def _fail(msg: str) -> None: print(f"  ✗  {msg}", file=sys.stderr)


async def reset(full: bool) -> None:
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        _fail("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)

    from supabase import acreate_client, AsyncClient
    client: AsyncClient = await acreate_client(url, key)

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    print("\n  Resetting demo data…\n")

    # ── Anomalies ─────────────────────────────────────────────────────────────
    try:
        await client.table("spending_anomalies").delete().in_(
            "workspace_id", [WORKSPACE_ID, WS_FROZEN_ID]
        ).execute()
        _ok("Deleted all spending_anomalies for demo workspaces")
    except Exception as exc:
        _fail(f"spending_anomalies: {exc}")

    # ── Budget alerts ─────────────────────────────────────────────────────────
    try:
        await client.table("workspace_budget_alerts").delete().in_(
            "workspace_id", [WORKSPACE_ID, WS_FROZEN_ID]
        ).eq("year", year).eq("month", month).execute()
        _ok("Deleted current-month budget_alerts for demo workspaces")
    except Exception as exc:
        _fail(f"budget_alerts: {exc}")

    # ── Current-month workspace usage ─────────────────────────────────────────
    try:
        await client.table("workspace_monthly_usage").delete().in_(
            "workspace_id", [WORKSPACE_ID, WS_FROZEN_ID]
        ).eq("year", year).eq("month", month).execute()
        _ok("Deleted current-month workspace_monthly_usage rows")
    except Exception as exc:
        _fail(f"workspace_monthly_usage: {exc}")

    # ── Current-month usage_counters ──────────────────────────────────────────
    period_start = datetime(year, month, 1, tzinfo=timezone.utc).isoformat()
    try:
        await client.table("usage_counters").delete().in_(
            "user_uuid", ALL_USERS
        ).eq("period_start", period_start).execute()
        _ok("Deleted current-month usage_counters for demo users")
    except Exception as exc:
        _fail(f"usage_counters: {exc}")

    # ── Restore user_quotas (full buckets, unsuspended) ───────────────────────
    tier_defaults = {
        USER_ALICE: {"current_tokens": 100_000},
        USER_BOB:   {"current_tokens":  10_000},
        USER_CAROL: {"current_tokens": 100_000},
        USER_DAVE:  {"current_tokens": 1_000_000},
        USER_EVE:   {"current_tokens": 100_000},
    }
    for uid, patch in tier_defaults.items():
        try:
            await client.table("user_quotas").update({
                **patch,
                "is_suspended":      False,
                "suspension_reason": None,
                "workspace_id":      None,
                "last_refill_at":    now.isoformat(),
            }).eq("user_uuid", uid).execute()
        except Exception as exc:
            _fail(f"quota restore {uid[:8]}…: {exc}")
    _ok("Restored user_quotas to full buckets, unsuspended")

    # ── Unsuspend workspaces ──────────────────────────────────────────────────
    try:
        await client.table("workspaces").update({
            "is_suspended":     False,
            "suspension_reason": None,
        }).in_("id", [WORKSPACE_ID, WS_FROZEN_ID]).execute()
        _ok("Unsuspended demo workspaces")
    except Exception as exc:
        _fail(f"unsuspend workspaces: {exc}")

    if full:
        print("\n  --full: removing workspace members, workspaces, user_quotas…\n")
        for table, col in [
            ("workspace_members",    "workspace_id"),
            ("workspace_monthly_usage", "workspace_id"),
            ("workspace_budget_alerts", "workspace_id"),
        ]:
            try:
                await client.table(table).delete().in_(
                    col, [WORKSPACE_ID, WS_FROZEN_ID]
                ).execute()
                _ok(f"Deleted all rows from {table}")
            except Exception as exc:
                _fail(f"{table}: {exc}")

        for uid in ALL_USERS:
            try:
                await client.table("user_quotas").delete().eq("user_uuid", uid).execute()
            except Exception:
                pass
        _ok("Deleted user_quotas for demo users")

        for ws_id in [WORKSPACE_ID, WS_FROZEN_ID]:
            try:
                await client.table("workspaces").delete().eq("id", ws_id).execute()
            except Exception:
                pass
        _ok("Deleted demo workspaces")

    print("\n  Reset complete.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Aivora Gatekeeper demo data.")
    parser.add_argument("--full",       action="store_true",
                        help="Also delete workspaces, members, and quota rows")
    parser.add_argument("--and-reseed", action="store_true",
                        help="Run seed_demo.py after reset")
    args = parser.parse_args()

    asyncio.run(reset(full=args.full))

    if args.and_reseed:
        seed_script = Path(__file__).parent / "seed_demo.py"
        print("  Running seed_demo.py…\n")
        result = subprocess.run([sys.executable, str(seed_script)], check=False)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
