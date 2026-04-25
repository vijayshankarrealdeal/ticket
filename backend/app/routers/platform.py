"""Platform-admin (super-user) endpoints.

The super-user lives above workspaces: they create organizations, pre-authorize
the email addresses that are allowed to sign in to each, and can eject members.
Their sessions are tracked in `platform_sessions` separately from regular user
sessions so a stolen workspace session can never pivot into platform admin.

Routes all sit under `/platform/*`.
"""
from __future__ import annotations

import hashlib
import secrets
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from passlib.hash import argon2

from ..config import SESSION_TTL_DAYS
from ..db import get_db

router = APIRouter(prefix="/platform", tags=["platform"])

# Dedicated cookie so the regular `wx_session` and the super-user session can
# coexist in the same browser (useful for dev / screenshot flows).
PLATFORM_COOKIE = "wx_platform_session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "org"
    return base[:40] + "-" + secrets.token_hex(3)


async def _admin_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    db = await get_db()
    cur = await db.execute(
        """
        SELECT pa.* FROM platform_sessions s
        JOIN platform_admins pa ON pa.id = s.admin_id
        WHERE s.token_hash = ? AND s.expires_at > now()
        """,
        (_hash_token(token),),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _require_admin(request: Request) -> dict:
    """Pull the platform-admin session token from the Authorization header or
    the dedicated cookie, verify it, and return the admin row. 401 otherwise."""
    authorization = request.headers.get("authorization", "")
    token = None
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(PLATFORM_COOKIE)
    admin = await _admin_from_token(token) if token else None
    if not admin:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated (platform)")
    return admin


# ---------- auth ----------

@router.post("/auth/signin")
async def platform_signin(body: dict, response: Response) -> dict:
    email = (body or {}).get("email", "").strip().lower()
    password = (body or {}).get("password", "")
    if not email or not password:
        raise HTTPException(400, "email and password required")
    db = await get_db()
    cur = await db.execute("SELECT * FROM platform_admins WHERE email = ?", (email,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(401, "invalid credentials")
    admin = dict(row)
    try:
        ok = argon2.verify(password, admin["password_hash"])
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(401, "invalid credentials")
    raw = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    await db.execute(
        "INSERT INTO platform_sessions (id, admin_id, token_hash, expires_at) VALUES (?,?,?,?)",
        (str(uuid4()), admin["id"], hashed, expires),
    )
    await db.commit()
    response.set_cookie(
        PLATFORM_COOKIE, raw, httponly=True, samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return {
        "admin": {"id": admin["id"], "name": admin["name"], "email": admin["email"]},
        "token": raw,
        "accessToken": raw,
    }


@router.post("/auth/signout")
async def platform_signout(request: Request, response: Response) -> dict:
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(PLATFORM_COOKIE)
    if token:
        db = await get_db()
        await db.execute(
            "DELETE FROM platform_sessions WHERE token_hash = ?", (_hash_token(token),)
        )
        await db.commit()
    response.delete_cookie(PLATFORM_COOKIE)
    return {"ok": True}


@router.get("/auth/me")
async def platform_me(request: Request) -> dict:
    admin = await _require_admin(request)
    return {"id": admin["id"], "name": admin["name"], "email": admin["email"]}


# ---------- organizations ----------

@router.get("/orgs")
async def list_orgs(request: Request) -> list[dict]:
    await _require_admin(request)
    db = await get_db()
    cur = await db.execute(
        """
        SELECT w.id, w.name, w.slug, w.created_at,
               (SELECT COUNT(*) FROM users u WHERE u.workspace_id = w.id) AS member_count,
               (SELECT COUNT(*) FROM workspace_allowed_emails a WHERE a.workspace_id = w.id) AS allowed_count
          FROM workspaces w
         ORDER BY w.created_at DESC
        """
    )
    return [dict(r) for r in await cur.fetchall()]


@router.post("/orgs")
async def create_org(body: dict, request: Request) -> dict:
    await _require_admin(request)
    name = (body or {}).get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    db = await get_db()
    ws_id = str(uuid4())
    slug = _slugify(name)
    await db.execute(
        "INSERT INTO workspaces (id, name, slug) VALUES (?,?,?)",
        (ws_id, name, slug),
    )
    await db.commit()
    return {"id": ws_id, "name": name, "slug": slug, "member_count": 0, "allowed_count": 0}


@router.delete("/orgs/{org_id}")
async def delete_org(org_id: str, request: Request) -> dict:
    """Delete a workspace and cascade to every row scoped to it (users,
    tickets, channels, messages, etc.). This is irreversible — the super-user
    must have already pulled any data they wanted to keep."""
    await _require_admin(request)
    db = await get_db()
    # Cascade from schema.sql takes care of downstream rows. Allowlist rows
    # hang off workspaces ON DELETE CASCADE too.
    cur = await db.execute("DELETE FROM workspaces WHERE id = ? RETURNING id", (org_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "org not found")
    await db.commit()
    return {"ok": True}


@router.get("/orgs/{org_id}/members")
async def list_members(org_id: str, request: Request) -> dict:
    """Return both the signed-up users AND the allowlist entries for an org.
    The allowlist is the source of truth for "who may sign in"; the users
    list is a subset of those who have actually activated."""
    await _require_admin(request)
    db = await get_db()
    cur = await db.execute("SELECT id, name FROM workspaces WHERE id = ?", (org_id,))
    ws = await cur.fetchone()
    if not ws:
        raise HTTPException(404, "org not found")
    cur = await db.execute(
        """
        SELECT id, name, email, role, status, provider, created_at
          FROM users WHERE workspace_id = ?
         ORDER BY created_at
        """,
        (org_id,),
    )
    users = [dict(r) for r in await cur.fetchall()]
    cur = await db.execute(
        """
        SELECT email, role, created_at,
               EXISTS (SELECT 1 FROM users u WHERE u.email = workspace_allowed_emails.email) AS activated
          FROM workspace_allowed_emails
         WHERE workspace_id = ?
         ORDER BY created_at DESC
        """,
        (org_id,),
    )
    allowed = [dict(r) for r in await cur.fetchall()]
    return {"org": dict(ws), "users": users, "allowed": allowed}


@router.post("/orgs/{org_id}/allowed-emails")
async def add_allowed_email(org_id: str, body: dict, request: Request) -> dict:
    admin = await _require_admin(request)
    email = (body or {}).get("email", "").strip().lower()
    role = (body or {}).get("role", "agent")
    if role not in ("viewer", "agent", "admin"):
        raise HTTPException(400, "invalid role")
    if not email or "@" not in email:
        raise HTTPException(400, "valid email required")
    db = await get_db()
    cur = await db.execute("SELECT 1 FROM workspaces WHERE id = ?", (org_id,))
    if not await cur.fetchone():
        raise HTTPException(404, "org not found")
    # An email can only belong to one org; PK on email enforces this.
    try:
        await db.execute(
            """
            INSERT INTO workspace_allowed_emails (email, workspace_id, role, invited_by)
            VALUES (?,?,?,?)
            """,
            (email, org_id, role, admin["id"]),
        )
        await db.commit()
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "primary" in msg:
            raise HTTPException(409, "that email is already approved for an organization")
        raise
    return {"email": email, "role": role, "workspace_id": org_id, "activated": False}


@router.delete("/orgs/{org_id}/allowed-emails/{email}")
async def remove_allowed_email(org_id: str, email: str, request: Request) -> dict:
    """Revoke an email's pre-authorization. Does NOT remove a user who already
    signed up — use DELETE /orgs/{id}/members/{uid} for that."""
    await _require_admin(request)
    db = await get_db()
    await db.execute(
        "DELETE FROM workspace_allowed_emails WHERE email = ? AND workspace_id = ?",
        (email.lower(), org_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/orgs/{org_id}/members/{user_id}")
async def remove_member(org_id: str, user_id: str, request: Request) -> dict:
    """Eject a user from the org. Also revokes their allowlist entry so they
    can't immediately re-sign up with the same email."""
    await _require_admin(request)
    db = await get_db()
    cur = await db.execute(
        "SELECT email FROM users WHERE id = ? AND workspace_id = ?",
        (user_id, org_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "member not found")
    email = (dict(row).get("email") or "").lower()
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    if email:
        await db.execute(
            "DELETE FROM workspace_allowed_emails WHERE email = ? AND workspace_id = ?",
            (email, org_id),
        )
    # Drop any lingering sessions so they're booted out immediately.
    await db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    return {"ok": True}
