"""OAuth sign-in + channel connect (spec §3.1, §6.1, §6.2).

Two modes, same machinery:

- **signin** — the user clicks "Continue with Google/Outlook" on /auth. We
  redirect to the provider's consent screen, exchange the code for an id_token,
  upsert a `users` row in a matching workspace (creating one on first login),
  and issue a session. Cookie is set, SPA redirected back with the bearer token
  in the URL fragment so the AuthPage can stash it in localStorage.

- **connect** — the user is already signed-in and clicks "Connect Gmail/Outlook"
  in Platforms. We redirect to the provider, grant `gmail.modify` / `Mail.Read`
  scopes, and persist a `platform_credentials` row so the outbox adapters can
  post mail. The workspace stays in whichever state the admin left it.

Dev mode: if `{PROVIDER}_CLIENT_ID` isn't configured, `start` redirects straight
to our own callback with a synthetic `code=dev` so operators can try the flow
offline. Real providers kick in the moment the env vars land.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from ..config import SESSION_COOKIE, SESSION_TTL_DAYS
from ..db import get_db, js
from ..deps import MaybeMe, Me

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/oauth", tags=["auth-oauth"])

# Back-origin the SPA lives on (Vite dev server). Used to build the success
# redirect URL so the AuthPage can pick up the session from the URL fragment.
SPA_ORIGIN = os.environ.get("SPA_ORIGIN", "http://localhost:5173")

PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "signin_scope": "openid email profile",
        "connect_scope": ("https://www.googleapis.com/auth/gmail.modify "
                          "https://www.googleapis.com/auth/gmail.send "
                          "openid email"),
        # First matching env var wins — `GOOGLE_CLIENT_ID` is the canonical
        # name, `Client_ID` is the generic fallback some .env files use.
        "client_id_envs": ["GOOGLE_CLIENT_ID", "Client_ID", "CLIENT_ID"],
        "client_secret_envs": ["GOOGLE_CLIENT_SECRET", "Client_Secret", "CLIENT_SECRET"],
        "connect_provider_key": "gmail",
    },
    "outlook": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "signin_scope": "openid email profile offline_access",
        "connect_scope": "openid email offline_access Mail.Read Mail.Send Mail.ReadWrite",
        "client_id_envs": ["OUTLOOK_CLIENT_ID", "MICROSOFT_CLIENT_ID"],
        "client_secret_envs": ["OUTLOOK_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET"],
        "connect_provider_key": "outlook",
    },
}


def _provider_creds(provider: str) -> tuple[str | None, str | None]:
    cfg = PROVIDERS[provider]
    cid = next((os.environ[k] for k in cfg["client_id_envs"] if os.environ.get(k)), None)
    sec = next((os.environ[k] for k in cfg["client_secret_envs"] if os.environ.get(k)), None)
    return cid, sec


def _backend_base(request: Request) -> str:
    """Origin the browser sees this backend on — important so the callback URL
    matches exactly what we register with the provider."""
    return os.environ.get("BACKEND_ORIGIN") or str(request.base_url).rstrip("/")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        hashlib.sha256(verifier.encode()).digest()
    )
    import base64
    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    return verifier, challenge_b64


async def _stash_state(*, state: str, provider: str, mode: str,
                       verifier: str, nonce: str, redirect_uri: str,
                       return_to: str | None,
                       workspace_id: str | None, user_id: str | None) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO oauth_states (state, provider, mode, workspace_id, user_id,
                                  code_verifier, nonce, redirect_uri, return_to)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (state, provider, mode, workspace_id, user_id, verifier, nonce,
         redirect_uri, return_to),
    )
    await db.commit()


async def _pop_state(state: str) -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM oauth_states WHERE state = ?", (state,)
    )
    r = await cur.fetchone()
    if not r:
        return None
    await db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    await db.commit()
    return dict(r)


@router.get("/{provider}/start")
async def oauth_start(
    provider: str,
    request: Request,
    mode: str = Query("signin"),
    return_to: str | None = Query(None),
    token: str | None = Query(None),
    user: dict | None = MaybeMe,
) -> RedirectResponse:
    if provider not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    if mode not in ("signin", "connect"):
        raise HTTPException(400, "mode must be signin or connect")
    # `connect` needs to know which workspace we're binding the channel to.
    # The cookie / Authorization header usually carries that, but when the SPA
    # drives a full-tab navigation straight to the backend origin neither is
    # sent (cross-origin). Fall back to a `?token=` query param so the SPA
    # can pass its bearer token on the URL.
    if mode == "connect" and not user and token:
        from .auth import session_user_from_token
        user = await session_user_from_token(token)
    if mode == "connect" and not user:
        raise HTTPException(401, "sign in before connecting a channel")

    cfg = PROVIDERS[provider]
    state = secrets.token_urlsafe(18)
    verifier, challenge = _pkce_pair()
    nonce = secrets.token_urlsafe(12)
    redirect_uri = f"{_backend_base(request)}/auth/oauth/{provider}/callback"

    await _stash_state(
        state=state, provider=provider, mode=mode,
        verifier=verifier, nonce=nonce, redirect_uri=redirect_uri,
        return_to=return_to,
        workspace_id=(user or {}).get("workspace_id") if mode == "connect" else None,
        user_id=(user or {}).get("id") if mode == "connect" else None,
    )

    client_id, _ = _provider_creds(provider)
    if not client_id:
        # Dev mode: redirect straight to our callback with a synthetic code so
        # the operator can exercise the success path without real credentials.
        return RedirectResponse(
            f"{redirect_uri}?code=dev&state={state}", status_code=302,
        )
    scope = cfg["signin_scope"] if mode == "signin" else cfg["connect_scope"]
    qs = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{cfg['auth_url']}?{qs}", status_code=302)


async def _exchange_code(provider: str, code: str, redirect_uri: str,
                          code_verifier: str) -> dict:
    cfg = PROVIDERS[provider]
    client_id, client_secret = _provider_creds(provider)
    if not client_id or code == "dev":
        # Dev-mode synthetic token — real providers never reach this branch.
        return {
            "access_token": f"dev_{secrets.token_hex(8)}",
            "refresh_token": f"dev_refresh_{secrets.token_hex(8)}",
            "id_token": None,
            "expires_in": 3600,
            "_dev": True,
        }
    try:
        import httpx  # type: ignore
    except ImportError:
        raise HTTPException(500, "httpx required for OAuth exchange")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(cfg["token_url"], data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }, headers={"Accept": "application/json"})
    if resp.status_code >= 400:
        raise HTTPException(400, f"token exchange failed: {resp.text[:200]}")
    return resp.json()


async def _fetch_userinfo(provider: str, access_token: str) -> dict:
    cfg = PROVIDERS[provider]
    if access_token.startswith("dev_"):
        # Synthetic profile so dev-mode sign-in still round-trips.
        slug = secrets.token_hex(3)
        return {
            "sub": f"dev-{slug}",
            "email": f"dev-{slug}@{provider}.local",
            "name": f"{provider.capitalize()} User {slug}",
            "picture": None,
        }
    try:
        import httpx  # type: ignore
    except ImportError:
        raise HTTPException(500, "httpx required for OAuth userinfo")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            cfg["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code >= 400:
        raise HTTPException(400, f"userinfo failed: {resp.text[:200]}")
    return resp.json()


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    if provider not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    rec = await _pop_state(state)
    if not rec:
        raise HTTPException(400, "state expired or invalid")
    tokens = await _exchange_code(provider, code, rec["redirect_uri"], rec["code_verifier"])
    userinfo = await _fetch_userinfo(provider, tokens.get("access_token") or "")

    db = await get_db()
    if rec["mode"] == "connect":
        # Store channel credentials under the existing user's workspace.
        ws_id = rec["workspace_id"]
        provider_key = PROVIDERS[provider]["connect_provider_key"]
        expires_at = None
        if tokens.get("expires_in"):
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))).isoformat()
        await db.execute(
            """
            INSERT INTO platform_credentials (workspace_id, provider, account_id,
                                              status, secrets, meta, installed_by, expires_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT (workspace_id, provider, account_id) DO UPDATE SET
              secrets = EXCLUDED.secrets, meta = EXCLUDED.meta,
              status = 'active', expires_at = EXCLUDED.expires_at
            """,
            (ws_id, provider_key, userinfo.get("email") or userinfo.get("sub"),
             "active", js(tokens),
             js({"email": userinfo.get("email"), "name": userinfo.get("name"),
                 "sub": userinfo.get("sub")}),
             rec["user_id"], expires_at),
        )
        # Reflect the connected state in `platforms` so the UI flips.
        await db.execute(
            """
            INSERT INTO platforms (id, workspace_id, kind, name, description,
                                   status, tickets_count, config)
            VALUES (?,?,?,?,?, 'connected', 0, ?)
            ON CONFLICT (workspace_id, id) DO UPDATE SET status = 'connected',
                                                         config = EXCLUDED.config
            """,
            (provider_key, ws_id,
             "email" if provider_key in ("gmail", "outlook") else "messaging",
             provider_key.title(),
             f"{provider.title()} mailbox connected",
             js({"email": userinfo.get("email")})),
        )
        await db.commit()
        return_to = rec.get("return_to") or f"{SPA_ORIGIN}/platforms"
        return RedirectResponse(f"{return_to}?connected={provider_key}", status_code=302)

    # ---- signin mode ----
    from passlib.hash import argon2  # lazy import so dev without argon2 still imports module
    email = (userinfo.get("email") or "").lower()
    if not email:
        raise HTTPException(400, "no email in userinfo")
    sub = userinfo.get("sub") or email

    # Upsert by provider+sub (primary) or email (fallback).
    cur = await db.execute(
        "SELECT * FROM users WHERE oauth_sub = ? AND provider = ? LIMIT 1",
        (sub, provider),
    )
    row = await cur.fetchone()
    if not row:
        cur = await db.execute(
            "SELECT * FROM users WHERE email = ? LIMIT 1", (email,)
        )
        row = await cur.fetchone()

    if not row:
        # First time — the email must be pre-authorized by the super-user for
        # exactly one workspace. No allowlist row ⇒ no auto-provisioned org.
        from .auth import resolve_allowed_email
        allowed = await resolve_allowed_email(db, email)
        if not allowed:
            # Bounce back to the sign-in screen with a readable error rather
            # than a raw 403 page from the OAuth callback.
            return_to = rec.get("return_to") or SPA_ORIGIN
            err = urllib.parse.quote("email_not_approved")
            return RedirectResponse(f"{return_to}/#/oauth-landing?error={err}", status_code=302)
        name = userinfo.get("name") or email.split("@")[0]
        uid = str(uuid4())
        await db.execute(
            """
            INSERT INTO users (id, workspace_id, name, email, provider, oauth_sub,
                               role, avatar_url, password_hash)
            VALUES (?,?,?,?,?,?, ?, ?, ?)
            """,
            (uid, allowed["workspace_id"], name, email, provider, sub,
             allowed.get("role") or "agent",
             userinfo.get("picture"),
             argon2.hash(secrets.token_urlsafe(16))),
        )
        await db.commit()
        user_id = uid
    else:
        user_id = row["id"]
        await db.execute(
            """
            UPDATE users SET provider = ?, oauth_sub = ?, avatar_url = COALESCE(?, avatar_url)
             WHERE id = ?
            """,
            (provider, sub, userinfo.get("picture"), user_id),
        )
        await db.commit()

    # Issue a fresh session (matches /auth/signin shape).
    import hashlib as _h
    raw_token = secrets.token_urlsafe(32)
    token_hash = _h.sha256(raw_token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    await db.execute(
        "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES (?,?,?,?)",
        (str(uuid4()), user_id, token_hash, expires_at),
    )
    await db.commit()

    # Redirect back to the SPA with the token in the URL fragment — AuthPage
    # parses it on mount and drops it into localStorage['workspacex.auth'].
    return_to = rec.get("return_to") or SPA_ORIGIN
    payload = urllib.parse.quote(f"token={raw_token}&provider={provider}&email={email}")
    resp = RedirectResponse(f"{return_to}/#/oauth-landing?{payload}", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE, raw_token, httponly=True, samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return resp


@router.post("/session-from-fragment")
async def session_from_fragment(body: dict) -> dict:
    """Companion endpoint the SPA hits after picking the raw token out of the
    OAuth-landing URL fragment. Returns the `users` row the SPA needs so it
    doesn't have to follow up with /auth/me."""
    token = (body or {}).get("token")
    if not token:
        raise HTTPException(400, "token required")
    from .auth import session_user_from_token, _to_user_out
    user = await session_user_from_token(token)
    if not user:
        raise HTTPException(401, "invalid token")
    return {"user": await _to_user_out(user), "token": token, "accessToken": token}
