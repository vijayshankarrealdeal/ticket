"""Auth — password + session cookies (spec §3.1).

OAuth (Google / Outlook) is stubbed: the start URL is returned but the flow
short-circuits in dev. Wire to the real providers using the recipes in BACKEND_SPEC.md §3.1.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from passlib.hash import argon2

from ..config import SESSION_COOKIE, SESSION_TTL_DAYS
from ..db import get_db
from ..schemas import AuthOut, SigninIn, SignupIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_token() -> tuple[str, str]:
    """→ (raw_token, token_hash). Store only the hash."""
    t = secrets.token_urlsafe(32)
    return t, _hash_token(t)


async def resolve_allowed_email(db, email: str) -> dict | None:
    """Look up an email in the workspace allowlist. Super-users pre-authorize
    every address that's allowed to sign in and pre-assign its role; anything
    not on the list cannot create an account at all. Returns the matching
    `{workspace_id, role}` row or None."""
    cur = await db.execute(
        "SELECT workspace_id, role FROM workspace_allowed_emails WHERE email = ?",
        (email,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def session_user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    db = await get_db()
    cur = await db.execute(
        """
        SELECT u.*, s.expires_at
        FROM auth_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at > now()
        """,
        (_hash_token(token),),
    )
    row = await cur.fetchone()
    await cur.close()
    return dict(row) if row else None


async def _to_user_out(user_row: dict, signed_in_at: str | None = None) -> dict:
    return {
        "id": user_row["id"],
        "name": user_row["name"],
        "email": user_row["email"],
        "role": user_row["role"],
        "provider": user_row["provider"],
        "workspaceId": user_row["workspace_id"],
        "signedInAt": signed_in_at or _now_iso(),
        "avatar_url": user_row.get("avatar_url"),
    }


async def _create_session(user_id: str, response: Response | None = None) -> str:
    db = await get_db()
    raw, hashed = _issue_token()
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    await db.execute(
        "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES (?,?,?,?)",
        (str(uuid4()), user_id, hashed, expires),
    )
    await db.commit()
    if response is not None:
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            httponly=True,
            samesite="lax",
            max_age=SESSION_TTL_DAYS * 24 * 3600,
        )
    return raw


@router.post("/signup", response_model=AuthOut)
async def signup(body: SignupIn, response: Response) -> AuthOut:
    db = await get_db()
    email = str(body.email).lower()
    cur = await db.execute("SELECT 1 FROM users WHERE email = ?", (email,))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="email already registered")

    # Allowlist gate — users can't create their own workspace; the super-user
    # must have added this email to exactly one org first. Role comes from the
    # allowlist row so admins are also chosen by the super-user.
    allowed = await resolve_allowed_email(db, email)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="this email has not been approved for any organization — contact your platform administrator",
        )
    workspace_id = allowed["workspace_id"]
    role = allowed.get("role") or "agent"

    user_id = str(uuid4())
    await db.execute(
        """
        INSERT INTO users (id, workspace_id, name, email, password_hash, provider, role)
        VALUES (?,?,?,?,?,?,?)
        """,
        (user_id, workspace_id, body.name, email,
         argon2.hash(body.password), "password", role),
    )
    await db.commit()

    cur = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user_row = dict(await cur.fetchone())

    token = await _create_session(user_id, response)
    user_out = await _to_user_out(user_row)
    return AuthOut(user=UserOut(**user_out), token=token, accessToken=token)


@router.post("/signin", response_model=AuthOut)
async def signin(body: SigninIn, response: Response) -> AuthOut:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM users WHERE email = ?", (str(body.email).lower(),)
    )
    row = await cur.fetchone()
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="invalid credentials")
    user = dict(row)
    try:
        ok = argon2.verify(body.password, user["password_hash"])
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = await _create_session(user["id"], response)
    user_out = await _to_user_out(user)
    return AuthOut(user=UserOut(**user_out), token=token, accessToken=token)


@router.post("/signout")
async def signout(request: Request, response: Response) -> dict:
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE)
    if token:
        db = await get_db()
        await db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_hash_token(token),))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=UserOut | None)
async def me(request: Request) -> dict | None:
    auth = request.headers.get("authorization", "")
    token = None
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE)
    user = await session_user_from_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return await _to_user_out(user)


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: dict) -> dict:
    """Issue a password-reset token. Real Build-3+ deployment delivers it via
    SMTP/SES; for now we echo the token in the response when `DEV_RESET_TOKEN`
    is on so the SPA's forgot-password form can test the round-trip end to
    end. Always returns 202 — never reveals whether the email exists."""
    import os
    email = (body or {}).get("email", "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    db = await get_db()
    cur = await db.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = await cur.fetchone()
    payload = {"ok": True}
    if row:
        raw = secrets.token_urlsafe(24)
        await db.execute(
            """
            INSERT INTO password_resets (user_id, token_hash, expires_at)
            VALUES (?, ?, now() + interval '1 hour')
            """,
            (row[0], _hash_token(raw)),
        )
        # Enqueue the reset email through the outbox so it's visible in
        # delivery_log alongside every other outbound message. The dispatcher
        # falls back to logging when SMTP isn't configured.
        cur2 = await db.execute("SELECT workspace_id FROM users WHERE id = ?", (row[0],))
        ws_row = await cur2.fetchone()
        ws_id = ws_row[0] if ws_row else None
        from .. import db as _db_mod  # local import to avoid cycles
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'email', ?)
            """,
            (ws_id, _db_mod.js({
                "workspace_id": ws_id,
                "to": email,
                "subject": "Reset your WorkspaceX password",
                "body": (
                    f"Use this token to reset your password: {raw}\n"
                    f"It expires in 1 hour. If you didn't request this, ignore."
                ),
                "kind": "password_reset",
            })),
        )
        await db.commit()
        if os.environ.get("DEV_RESET_TOKEN", "1") == "1":
            payload["devToken"] = raw
    return payload


@router.post("/reset-password")
async def reset_password(body: dict) -> dict:
    """Consume a password-reset token — verify the hash, enforce single-use
    via `used_at`, rehash the new password, and drop every outstanding
    session for that user so other tabs get bounced back to sign-in."""
    token = (body or {}).get("token") or ""
    new = (body or {}).get("new") or ""
    if len(new) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, user_id, expires_at, used_at FROM password_resets
         WHERE token_hash = ?
        """,
        (_hash_token(token),),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(400, "invalid or expired token")
    r = dict(row)
    if r.get("used_at"):
        raise HTTPException(400, "token already used")
    exp = r.get("expires_at")
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            exp_dt = None
    else:
        exp_dt = exp
    if exp_dt and exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if exp_dt and exp_dt < datetime.now(timezone.utc):
        raise HTTPException(400, "token expired")

    new_hash = argon2.hash(new)
    await db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, r["user_id"])
    )
    await db.execute(
        "UPDATE password_resets SET used_at = now() WHERE id = ?", (r["id"],)
    )
    await db.execute(
        "DELETE FROM auth_sessions WHERE user_id = ?", (r["user_id"],)
    )
    await db.commit()
    return {"ok": True}


# OAuth sign-in lives in routers/oauth.py — providers {google, outlook}.
# /auth/oauth/{provider}/start + /callback are parameterized there.
