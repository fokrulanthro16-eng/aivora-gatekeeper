"""
Polar.sh subscription service.

Responsibilities:
  1. Verify inbound webhook signatures (HMAC-SHA256 per webhooks.fyi spec).
  2. Persist raw events to polar_webhook_events for idempotent replay.
  3. Process subscription lifecycle events and keep the subscriptions table
     in sync with Polar's state.
  4. Update user_quotas.billing_tier_id when a user's tier changes.

Polar webhook headers:
  webhook-id         — unique event ID (idempotency key)
  webhook-timestamp  — Unix epoch seconds
  webhook-signature  — "v1,<base64-encoded-HMAC-SHA256>"
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import structlog
from typing import Any

from app.core.config import get_settings

log = structlog.get_logger(__name__)

# Map Polar product/price names → billing_tier_id in billing_tiers table
# Adjust these strings to match your actual Polar product names.
POLAR_TIER_MAP: dict[str, int] = {
    "free":       1,
    "pro":        2,
    "enterprise": 3,
}

# Tolerance window for webhook timestamp replay-attack prevention (5 minutes)
_TIMESTAMP_TOLERANCE_SECONDS = 300


class PolarSignatureError(Exception):
    """Raised when a webhook signature cannot be verified."""


def verify_webhook_signature(
    raw_body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
) -> None:
    """
    Verify a Polar.sh webhook signature.

    Raises PolarSignatureError if:
      • POLAR_WEBHOOK_SECRET is not configured
      • The timestamp is outside the replay-attack tolerance window
      • The computed signature does not match any signature in the header

    The signed message follows the webhooks.fyi specification:
        "{webhook_id}.{webhook_timestamp}.{body}"
    """
    secret = get_settings().POLAR_WEBHOOK_SECRET
    if not secret:
        raise PolarSignatureError(
            "POLAR_WEBHOOK_SECRET is not configured. "
            "Set it in your .env file to enable webhook verification."
        )

    # Replay-attack prevention
    try:
        ts = int(webhook_timestamp)
    except ValueError as exc:
        raise PolarSignatureError(f"Invalid webhook-timestamp: {webhook_timestamp!r}") from exc

    delta = abs(time.time() - ts)
    if delta > _TIMESTAMP_TOLERANCE_SECONDS:
        raise PolarSignatureError(
            f"Webhook timestamp is {delta:.0f}s outside the "
            f"{_TIMESTAMP_TOLERANCE_SECONDS}s tolerance window."
        )

    # Build the signed message
    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body

    # Compute expected HMAC-SHA256
    mac = hmac.new(secret.encode(), signed_content, hashlib.sha256)
    expected = base64.b64encode(mac.digest()).decode()

    # The header may carry multiple comma-separated "v1,<sig>" tokens
    for token in webhook_signature.split(" "):
        token = token.strip()
        if not token.startswith("v1,"):
            continue
        received = token[3:]
        if hmac.compare_digest(received, expected):
            return  # valid

    raise PolarSignatureError("Webhook signature verification failed.")


def is_polar_configured() -> bool:
    s = get_settings()
    return bool(s.POLAR_WEBHOOK_SECRET) and bool(s.POLAR_ACCESS_TOKEN)


def resolve_tier_id(product_name: str | None, price_type: str | None = None) -> int:
    """
    Map a Polar product name or price type to a billing_tier_id.
    Returns 1 (Free) as the safe default if the product is unrecognised.
    """
    if product_name:
        key = product_name.lower().strip()
        for fragment, tier_id in POLAR_TIER_MAP.items():
            if fragment in key:
                return tier_id
    if price_type:
        key = price_type.lower().strip()
        for fragment, tier_id in POLAR_TIER_MAP.items():
            if fragment in key:
                return tier_id
    return 1  # default: Free


def build_subscription_upsert(
    user_uuid: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Build a dict of column values for an upsert into the subscriptions table.
    Returns None when the event type is not subscription-related.

    Supported event types:
      subscription.created
      subscription.updated
      subscription.active
      subscription.cancelled
      subscription.revoked
    """
    subscription_events = {
        "subscription.created",
        "subscription.updated",
        "subscription.active",
        "subscription.cancelled",
        "subscription.revoked",
    }
    if event_type not in subscription_events:
        return None

    data = payload.get("data", {})
    polar_sub_id    = data.get("id")
    polar_cust_id   = data.get("customer_id")
    product         = data.get("product", {})
    product_name    = product.get("name") if isinstance(product, dict) else None
    status_raw      = data.get("status", "active")
    period_start    = data.get("current_period_start")
    period_end      = data.get("current_period_end")
    cancel_at_end   = data.get("cancel_at_period_end", False)
    cancelled_at    = data.get("cancelled_at")

    # Map Polar status values to our allowed status set
    status_map = {
        "active":     "active",
        "trialing":   "trialing",
        "past_due":   "past_due",
        "canceled":   "cancelled",   # Polar uses "canceled" (American spelling)
        "cancelled":  "cancelled",
        "revoked":    "cancelled",
        "incomplete": "incomplete",
        "paused":     "paused",
    }
    status = status_map.get(status_raw, "active")
    tier_id = resolve_tier_id(product_name)

    return {
        "user_uuid":               user_uuid,
        "tier_id":                 tier_id,
        "polar_subscription_id":   polar_sub_id,
        "polar_customer_id":       polar_cust_id,
        "status":                  status,
        "current_period_start":    period_start,
        "current_period_end":      period_end,
        "cancel_at_period_end":    cancel_at_end,
        "cancelled_at":            cancelled_at,
    }
