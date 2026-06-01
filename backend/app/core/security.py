"""
JWT verification and RBAC FastAPI dependencies.

Authentication flow
-------------------
1. The caller sends  Authorization: Bearer <supabase_jwt>
2. get_current_user_uuid() verifies the signature with SUPABASE_JWT_SECRET,
   checks expiry and audience, and returns the user UUID from the `sub` claim.
3. workspace_role_dep(min_role) returns a dependency that:
   a. Calls get_current_user_uuid
   b. Queries workspace_members to find the caller's role
   c. Raises 403 if the role is below the required minimum

Admin access
------------
require_admin_jwt() checks app_metadata.is_admin == True in the JWT claims.
Set this via the Supabase dashboard (Authentication → Users → Edit user) or:
    UPDATE auth.users
    SET raw_app_meta_data = raw_app_meta_data || '{"is_admin":true}'
    WHERE id = '<uuid>';

Configuration
-------------
SUPABASE_JWT_SECRET  required — the JWT secret from Supabase project Settings → API.
                     If empty the workspace and admin routes return 503.
"""
from __future__ import annotations

import asyncio
from typing import Literal

import jwt
import structlog
from fastapi import Depends, Header, HTTPException
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.core.config import get_settings
from app.services.supabase_client import get_supabase_client, is_supabase_available

log = structlog.get_logger(__name__)

_ROLE_LEVEL: dict[str, int] = {"owner": 3, "admin": 2, "member": 1}


# ── Token verification ────────────────────────────────────────────────────────

def _decode_jwt(token: str) -> dict:
    """
    Decode and verify a Supabase JWT.  Raises HTTPException on all failure modes.
    """
    secret = get_settings().SUPABASE_JWT_SECRET
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured on this server. Set SUPABASE_JWT_SECRET.",
        )
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["sub", "exp", "aud"]},
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="JWT has expired.")
    except InvalidTokenError as exc:
        log.warning("jwt_invalid", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid JWT.")


def _extract_bearer(authorization: str) -> str:
    """Parse 'Bearer <token>' and return the raw token string."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Bearer <token>",
        )
    return authorization[7:].strip()


# ── FastAPI dependencies ──────────────────────────────────────────────────────

async def get_current_user_uuid(
    authorization: str = Header(default="", alias="Authorization"),
) -> str:
    """
    FastAPI dependency — verifies the Bearer JWT and returns the user UUID (sub claim).
    Raises 401 if the token is missing, expired, or invalid.
    """
    token   = _extract_bearer(authorization)
    payload = _decode_jwt(token)
    user_uuid: str | None = payload.get("sub")
    if not user_uuid:
        raise HTTPException(status_code=401, detail="JWT is missing 'sub' claim.")
    return user_uuid


async def require_admin_jwt(
    authorization: str = Header(default="", alias="Authorization"),
) -> str:
    """
    FastAPI dependency — verifies the JWT AND checks app_metadata.is_admin == True.
    Returns the admin user UUID.
    Raises 401 for auth failures, 403 if the user is not an admin.
    """
    token   = _extract_bearer(authorization)
    payload = _decode_jwt(token)
    user_uuid: str | None = payload.get("sub")
    if not user_uuid:
        raise HTTPException(status_code=401, detail="JWT is missing 'sub' claim.")

    app_meta: dict = payload.get("app_metadata") or {}
    if not app_meta.get("is_admin"):
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Set app_metadata.is_admin=true on your user account.",
        )
    return user_uuid


class WorkspaceRoleChecker:
    """
    Class-based FastAPI dependency that verifies workspace membership and enforces
    a minimum role level.

    Usage in route:
        @router.get("/{workspace_id}/members")
        async def list_members(
            workspace_id: str,
            _: str = Depends(WorkspaceRoleChecker("member")),
        ):
            ...
    """

    def __init__(self, min_role: Literal["owner", "admin", "member"] = "member") -> None:
        self.min_level = _ROLE_LEVEL[min_role]
        self.min_role  = min_role

    async def __call__(
        self,
        workspace_id: str,
        user_uuid: str = Depends(get_current_user_uuid),
    ) -> str:
        """Returns user_uuid after verifying their role in the workspace."""
        role = await _get_workspace_role(user_uuid, workspace_id)
        if role is None:
            raise HTTPException(
                status_code=403,
                detail="You are not a member of this workspace.",
            )
        if _ROLE_LEVEL.get(role, 0) < self.min_level:
            raise HTTPException(
                status_code=403,
                detail=f"This action requires '{self.min_role}' role or higher.",
            )
        return user_uuid


async def _get_workspace_role(user_uuid: str, workspace_id: str) -> str | None:
    """Query workspace_members for the user's role.  Returns None if not a member."""
    if not is_supabase_available():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — cannot verify workspace membership.",
        )
    try:
        client = get_supabase_client()
        result = await asyncio.wait_for(
            client.table("workspace_members")
                .select("role")
                .eq("workspace_id", workspace_id)
                .eq("user_uuid", user_uuid)
                .maybe_single()
                .execute(),
            timeout=3.0,
        )
        if result.data:
            return result.data["role"]
        return None
    except HTTPException:
        raise
    except Exception as exc:
        log.error("workspace_role_lookup_error", workspace_id=workspace_id,
                  user_uuid=user_uuid, error=str(exc))
        raise HTTPException(status_code=503, detail="Could not verify workspace role.")
