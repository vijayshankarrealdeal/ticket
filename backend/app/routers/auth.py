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
    cur = await db.execute("SELECT 1 FROM users WHERE email = ?", (str(body.email).lower(),))
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="email already registered")

    # Sign-up creates a fresh workspace for this user (first-admin flow).
    workspace_id = str(uuid4())
    slug = str(body.email).split("@")[0].lower() + "-" + secrets.token_hex(3)
    await db.execute(
        "INSERT INTO workspaces (id, name, slug) VALUES (?,?,?)",
        (workspace_id, f"{body.name}'s Workspace", slug),
    )

    user_id = str(uuid4())
    await db.execute(
        """
        INSERT INTO users (id, workspace_id, name, email, password_hash, provider, role)
        VALUES (?,?,?,?,?,?,?)
        """,
        (user_id, workspace_id, body.name, str(body.email).lower(),
         argon2.hash(body.password), "password", "admin"),
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
    # Stub — email sending is out of scope for Build 1. See spec §3.1.
    return {"ok": True}


# Placeholders for OAuth start/callback; real flow in BACKEND_SPEC.md §3.1.
@router.get("/oauth/google/start")
async def google_start() -> dict:
    return {"ok": False, "detail": "oauth not configured in build 1"}


@router.get("/oauth/outlook/start")
async def outlook_start() -> dict:
    return {"ok": False, "detail": "oauth not configured in build 1"}
