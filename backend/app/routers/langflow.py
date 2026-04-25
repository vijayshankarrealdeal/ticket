"""Langflow integration (spec §3.11.A–E).

Three surfaces:

- `GET /flow/launch[?agent=<id>]` — admin-token SSO handoff. On first visit we
  provision a Langflow user + folder for the caller, issue an access token
  against Langflow's `/api/v1/login`, drop it into an `access_token_lf`
  cookie, and 302 the browser into the Langflow UI, optionally deep-linking
  into the draft flow for `<agent>`. Dev mode (no `LANGFLOW_URL`) redirects
  to a placeholder HTML page so the AgentBuilder "Open Langflow" button
  still round-trips.
- `POST /webhooks/langflow/published` — HMAC-verified publish hook. Langflow
  POSTs the new flow JSON when an author clicks "Publish"; we upsert an
  `agent_flow_snapshots` row + flip the matching `agents.status='published'`
  + `is_active=true` so `select_inbound_agent` picks it up on the next
  inbound. Falls back to the pending poll loop when the hook isn't reachable.
- Publish polling cron in `worker.py` — diff `agents.updated_at` per folder
  every 30 s as a safety net for environments where the webhook can't reach
  the API.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import get_db, js
from ..deps import MaybeMe
from ..ws import broker
from .auth import session_user_from_token

log = logging.getLogger(__name__)

launch_router = APIRouter(prefix="/flow", tags=["langflow"])
webhook_router = APIRouter(tags=["langflow-webhooks"])


LANGFLOW_URL = os.environ.get("LANGFLOW_URL")                 # e.g. http://langflow:7860
# URL the browser uses to reach Langflow. May differ from LANGFLOW_URL when
# the backend is in docker-compose (internal DNS) but the browser is on the
# host. Falls back to LANGFLOW_URL when unset.
LANGFLOW_PUBLIC_URL = os.environ.get("LANGFLOW_PUBLIC_URL") or LANGFLOW_URL
LANGFLOW_SUPERUSER = os.environ.get("LANGFLOW_SUPERUSER") or "admin"
LANGFLOW_SUPERUSER_PASSWORD = os.environ.get("LANGFLOW_SUPERUSER_PASSWORD")
LANGFLOW_WEBHOOK_SECRET = os.environ.get("LANGFLOW_WEBHOOK_SECRET")


def _fernet():
    """Symmetric cipher for the per-user Langflow password we stash in
    `users.langflow_password_secret_id`. Keyed off `WORKSPACEX_SECRET_KEY`
    so admins can rotate without wiping the column. Falls back to a
    deterministic app-wide key derived from `SESSION_COOKIE` when none is
    set — fine for dev; production should set the real secret."""
    import base64, hashlib
    seed = (os.environ.get("WORKSPACEX_SECRET_KEY")
            or os.environ.get("SECRET_KEY")
            or "workspacex-dev-key").encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    try:
        from cryptography.fernet import Fernet  # type: ignore
        return Fernet(key)
    except ImportError:
        return None


def _encrypt(secret: str) -> str:
    f = _fernet()
    if f is None:
        # No cryptography lib — fall back to a short XOR+hex so the value
        # isn't plaintext at rest. Weaker than Fernet; upgrade by installing
        # `cryptography` and restarting.
        import base64, hashlib
        key = hashlib.sha256(b"wx-lf").digest()
        b = secret.encode()
        xored = bytes(c ^ key[i % len(key)] for i, c in enumerate(b))
        return "v1:" + base64.urlsafe_b64encode(xored).decode()
    return "v2:" + f.encrypt(secret.encode()).decode()


def _decrypt(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        if blob.startswith("v2:"):
            f = _fernet()
            if f is None:
                return None
            return f.decrypt(blob[3:].encode()).decode()
        if blob.startswith("v1:"):
            import base64, hashlib
            key = hashlib.sha256(b"wx-lf").digest()
            b = base64.urlsafe_b64decode(blob[3:].encode())
            return bytes(c ^ key[i % len(key)] for i, c in enumerate(b)).decode()
    except Exception:
        return None
    return None


async def _superuser_bearer() -> str | None:
    """Log in as the configured Langflow superuser and return the JWT. Used
    for admin operations (create user, create folder) that need elevated
    privileges — we never expose this token to the browser."""
    if not LANGFLOW_URL or not LANGFLOW_SUPERUSER_PASSWORD:
        return None
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{LANGFLOW_URL}/api/v1/login",
                data={"username": LANGFLOW_SUPERUSER,
                      "password": LANGFLOW_SUPERUSER_PASSWORD},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 400:
            log.warning("langflow superuser login failed: %s %s",
                        resp.status_code, resp.text[:200])
            return None
        return (resp.json() or {}).get("access_token")
    except Exception:
        log.exception("langflow superuser login raised")
        return None


async def _provision_user_and_folder(db, user: dict) -> tuple[str, str, str | None]:
    """Ensure the Langflow user + per-workspace folder exist, returning
    `(langflow_user_id, langflow_folder_id, generated_password_or_None)`.

    The generated password is only returned when we've just created the user
    (so the caller can log in with it immediately); on subsequent calls we
    return None and the caller reads the stashed encrypted password from the
    users row.
    """
    # Read everything in one shot — both stored ids and the stashed password.
    cur = await db.execute(
        """
        SELECT u.langflow_user_id, u.langflow_password_secret_id, w.langflow_folder_id
          FROM users u JOIN workspaces w ON w.id = u.workspace_id
         WHERE u.id = ?
        """,
        (user["id"],),
    )
    row = dict(await cur.fetchone() or {})
    lf_user_id = row.get("langflow_user_id")
    lf_folder_id = row.get("langflow_folder_id")
    stashed = _decrypt(row.get("langflow_password_secret_id"))

    # Dev mode — no LF, synthetic ids are fine.
    if not LANGFLOW_URL:
        lf_user_id = lf_user_id or str(uuid4())
        lf_folder_id = lf_folder_id or str(uuid4())
        await db.execute(
            "UPDATE users SET langflow_user_id = ? WHERE id = ?",
            (lf_user_id, user["id"]),
        )
        await db.execute(
            "UPDATE workspaces SET langflow_folder_id = ? WHERE id = ?",
            (lf_folder_id, user["workspace_id"]),
        )
        await db.commit()
        return str(lf_user_id), str(lf_folder_id), None

    # Happy path: we already have ids and a stashed password the bridge can
    # replay. Skip all the admin-API calls.
    if lf_user_id and lf_folder_id and stashed:
        return str(lf_user_id), str(lf_folder_id), None

    admin_bearer = await _superuser_bearer()
    if not admin_bearer:
        raise HTTPException(503, "langflow superuser not configured — set "
                                  "LANGFLOW_SUPERUSER_PASSWORD on the backend")

    new_password = uuid4().hex + uuid4().hex
    try:
        import httpx  # type: ignore
        headers = {"Authorization": f"Bearer {admin_bearer}",
                   "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            # Step 1 — resolve the real Langflow user id for this email, no
            # matter what's stored. `langflow_user_id` may be a synthetic UUID
            # left over from dev-mode provisioning; Langflow doesn't know it.
            real_lf_user_id = await _lookup_lf_user_by_email(client, user["email"])
            if not real_lf_user_id:
                create = await client.post(
                    f"{LANGFLOW_URL}/api/v1/users/",
                    json={"username": user["email"], "password": new_password,
                          "is_active": True, "is_superuser": False},
                )
                if create.status_code < 400:
                    real_lf_user_id = (create.json() or {}).get("id")
                else:
                    # Most likely "username exists" — re-list and pick it up.
                    real_lf_user_id = await _lookup_lf_user_by_email(client, user["email"])

            if not real_lf_user_id:
                raise HTTPException(502, "Langflow user provisioning failed "
                                         "(check superuser creds + /api/v1/users/ reachability)")

            lf_user_id = real_lf_user_id

            # Step 2 — (re)set that user's password so we can log in as them.
            # Langflow exposes PATCH /api/v1/users/{id} with {password} when
            # the caller is a superuser. Some builds also accept the bespoke
            # `/reset-password` endpoint — we try PATCH first and fall back.
            reset_ok = False
            patch = await client.patch(
                f"{LANGFLOW_URL}/api/v1/users/{lf_user_id}",
                json={"password": new_password,
                      "is_active": True},
            )
            if patch.status_code < 400:
                reset_ok = True
            else:
                alt = await client.patch(
                    f"{LANGFLOW_URL}/api/v1/users/{lf_user_id}/reset-password",
                    json={"password": new_password},
                )
                reset_ok = alt.status_code < 400
                if not reset_ok:
                    log.warning("langflow password reset failed: PATCH=%s %s / reset=%s %s",
                                patch.status_code, patch.text[:160],
                                alt.status_code, alt.text[:160])
            if not reset_ok:
                raise HTTPException(502, "could not reset Langflow user password")

            # Step 3 — ensure the workspace folder exists.
            if not lf_folder_id:
                f = await client.post(
                    f"{LANGFLOW_URL}/api/v1/folders/",
                    json={"name": f"ws-{user['workspace_id']}",
                          "description": f"Workspace {user['workspace_id']}"},
                )
                if f.status_code < 400:
                    lf_folder_id = (f.json() or {}).get("id")
    except HTTPException:
        raise
    except Exception:
        log.exception("langflow provisioning failed")
        raise HTTPException(502, "langflow provisioning failed (see backend logs)")

    if not lf_folder_id:
        lf_folder_id = str(uuid4())  # best-effort; Langflow will still render

    await db.execute(
        "UPDATE users SET langflow_user_id = ?, langflow_password_secret_id = ? WHERE id = ?",
        (lf_user_id, _encrypt(new_password), user["id"]),
    )
    await db.execute(
        "UPDATE workspaces SET langflow_folder_id = ? WHERE id = ?",
        (lf_folder_id, user["workspace_id"]),
    )
    await db.commit()
    return str(lf_user_id), str(lf_folder_id), new_password


async def ensure_flow_alive(db, *, agent_id: str, workspace_id: str) -> str | None:
    """Guarantee the agent's Langflow flow actually exists in Langflow.

    Langflow's container DB is ephemeral in most deploys — restarting it wipes
    every flow, leaving a dangling `agents.langflow_flow_id`. MinIO holds the
    canonical config (written by `/flow/save-as-agent`), so we:

      1. Resolve the owner's LF user id + folder (provision if missing).
      2. HEAD the stored flow id. If it's alive, return it unchanged.
      3. Otherwise pull the latest config from MinIO (falling back to the
         Postgres `agent_flow_snapshots` row) and re-POST it to
         `/api/v1/flows/`. Update `agents.langflow_flow_id` with whatever id
         Langflow hands us back so future calls go straight through.

    Returns the live flow id, or None if we can't resurrect it.
    """
    if not LANGFLOW_URL:
        return None
    cur = await db.execute(
        """
        SELECT a.id, a.name, a.langflow_flow_id, a.flow_s3_key, a.workspace_id,
               a.created_by, w.langflow_folder_id
          FROM agents a JOIN workspaces w ON w.id = a.workspace_id
         WHERE a.id = ? AND a.workspace_id = ?
        """,
        (agent_id, workspace_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    current_flow_id = r.get("langflow_flow_id")
    folder_id = r.get("langflow_folder_id")

    # Resolve the user that owns this flow. Prefer the agent's `created_by`;
    # fall back to any workspace admin so chat still works when audit metadata
    # isn't populated (older agents).
    owner_id = r.get("created_by")
    owner: dict | None = None
    if owner_id:
        cur = await db.execute(
            "SELECT id, email, workspace_id, role FROM users WHERE id = ?",
            (owner_id,),
        )
        o = await cur.fetchone()
        owner = dict(o) if o else None
    if not owner:
        cur = await db.execute(
            "SELECT id, email, workspace_id, role FROM users "
            "WHERE workspace_id = ? AND role IN ('admin','owner') LIMIT 1",
            (workspace_id,),
        )
        o = await cur.fetchone()
        owner = dict(o) if o else None
    if not owner:
        log.warning("ensure_flow_alive: no owner user for workspace %s", workspace_id)
        return None

    # Provision (idempotent) so we always have a user+folder even when this is
    # the first time we've touched this workspace since a Langflow wipe.
    try:
        lf_user_id, lf_folder_id, _ = await _provision_user_and_folder(db, owner)
    except HTTPException:
        raise
    except Exception:
        log.exception("ensure_flow_alive: provisioning raised")
        return None
    folder_id = folder_id or lf_folder_id

    admin_bearer = await _superuser_bearer()
    if not admin_bearer:
        return current_flow_id

    try:
        import httpx  # type: ignore
    except ImportError:
        return current_flow_id

    async with httpx.AsyncClient(
        timeout=15,
        headers={"Authorization": f"Bearer {admin_bearer}",
                 "Content-Type": "application/json"},
    ) as client:
        # HEAD the existing flow; 200 means Langflow still has it.
        if current_flow_id:
            try:
                resp = await client.get(f"{LANGFLOW_URL}/api/v1/flows/{current_flow_id}")
                if resp.status_code < 400:
                    return str(current_flow_id)
            except Exception:
                pass

        # Load the saved config — MinIO first, DB snapshot as fallback.
        config = None
        try:
            from ..s3 import get_json as _s3_get_json
            key = r.get("flow_s3_key") or f"flows/{workspace_id}/{agent_id}/latest.json"
            config = _s3_get_json(key)
        except Exception:
            log.exception("ensure_flow_alive: minio read raised")

        if not config:
            cur = await db.execute(
                "SELECT flow_json, version FROM agent_flow_snapshots "
                "WHERE agent_id = ? ORDER BY version DESC LIMIT 1",
                (agent_id,),
            )
            snap = await cur.fetchone()
            if snap:
                s = dict(snap)
                raw = s.get("flow_json")
                try:
                    config = {"data": json.loads(raw) if isinstance(raw, str) else raw,
                              "version": s.get("version")}
                except Exception:
                    config = {"data": raw or {}, "version": s.get("version")}

        if not config or not isinstance(config, dict):
            log.warning("ensure_flow_alive: no stored config for %s", agent_id)
            return None

        flow_body = {
            "name": config.get("name") or r.get("name") or f"Flow {agent_id[:8]}",
            "description": f"Rehydrated from MinIO for agent {agent_id}",
            "folder_id": folder_id,
            "user_id": lf_user_id,
            "data": config.get("data") or {},
            "is_component": False,
        }

        # Try restoring at the original id first so the run url stays stable.
        new_flow_id: str | None = None
        if current_flow_id:
            try:
                put = await client.request(
                    "POST",
                    f"{LANGFLOW_URL}/api/v1/flows/",
                    json={**flow_body, "id": current_flow_id},
                )
                if put.status_code < 400:
                    body = put.json() or {}
                    new_flow_id = body.get("id") or current_flow_id
            except Exception:
                pass
        if not new_flow_id:
            try:
                post = await client.post(f"{LANGFLOW_URL}/api/v1/flows/", json=flow_body)
                if post.status_code < 400:
                    new_flow_id = (post.json() or {}).get("id")
                else:
                    log.warning("ensure_flow_alive: restore failed %s %s",
                                post.status_code, post.text[:200])
            except Exception:
                log.exception("ensure_flow_alive: restore raised")

        if not new_flow_id:
            return None

    if new_flow_id != current_flow_id:
        await db.execute(
            "UPDATE agents SET langflow_flow_id = ?, langflow_folder_id = ? "
            "WHERE id = ? AND workspace_id = ?",
            (new_flow_id, folder_id, agent_id, workspace_id),
        )
        await db.commit()
    return str(new_flow_id)


async def _ensure_agent_flow(db, *, agent_id: str, workspace_id: str,
                              lf_user_id: str, lf_folder_id: str) -> str | None:
    """Create a blank Langflow flow for this agent if it doesn't have one
    yet, return its Langflow flow id. No-ops (returns the existing id) when
    the agent already points at a flow — keeps the SSO path idempotent."""
    cur = await db.execute(
        "SELECT name, langflow_flow_id FROM agents WHERE id = ? AND workspace_id = ?",
        (agent_id, workspace_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    r = dict(row)
    if r.get("langflow_flow_id"):
        return str(r["langflow_flow_id"])

    admin_bearer = await _superuser_bearer()
    if not admin_bearer:
        return None

    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(
            timeout=15,
            headers={"Authorization": f"Bearer {admin_bearer}",
                     "Content-Type": "application/json"},
        ) as client:
            resp = await client.post(
                f"{LANGFLOW_URL}/api/v1/flows/",
                json={
                    "name": r.get("name") or "New agent",
                    "description": f"WorkspaceX agent {agent_id}",
                    "folder_id": lf_folder_id,
                    "user_id": lf_user_id,
                    "data": {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
                    "is_component": False,
                },
            )
    except Exception:
        log.exception("langflow auto-create flow raised")
        return None

    if resp.status_code >= 400:
        log.warning("langflow auto-create flow failed: %s %s",
                    resp.status_code, resp.text[:200])
        return None

    body = resp.json() or {}
    flow_id = body.get("id")
    if not flow_id:
        return None

    await db.execute(
        "UPDATE agents SET langflow_flow_id = ?, langflow_folder_id = ?, kind = 'langflow' "
        "WHERE id = ? AND workspace_id = ?",
        (flow_id, lf_folder_id, agent_id, workspace_id),
    )
    await db.commit()
    return str(flow_id)


async def _lookup_lf_user_by_email(client, email: str) -> str | None:
    """GET /api/v1/users/ as the superuser, scan for `username == email`.
    Returns the Langflow user's UUID or None. Tolerant to different response
    shapes (list, {users: […]}, {items: […]}) across Langflow versions."""
    try:
        r = await client.get(f"{LANGFLOW_URL}/api/v1/users/",
                             params={"size": 200})
    except Exception:
        return None
    if r.status_code >= 400:
        return None
    entries = r.json() or []
    if isinstance(entries, dict):
        entries = entries.get("users") or entries.get("items") or []
    target = (email or "").lower()
    for e in entries:
        if (e.get("username") or "").lower() == target:
            return e.get("id")
    return None


async def _mint_access_token(db, user: dict, password_hint: str | None) -> str | None:
    """Log into Langflow as this specific WorkspaceX user — not the superuser
    — so their session in Langflow belongs to them and permission checks
    line up. Uses the password generated at provisioning time (or supplied
    via `password_hint` on a fresh provisioning) and stashed encrypted in
    `users.langflow_password_secret_id`."""
    if not LANGFLOW_URL:
        return None

    password = password_hint
    if not password:
        cur = await db.execute(
            "SELECT langflow_password_secret_id FROM users WHERE id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        if row:
            password = _decrypt(dict(row).get("langflow_password_secret_id"))
    if not password:
        log.warning("langflow: no stashed password for user %s — cannot SSO", user["id"])
        return None

    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{LANGFLOW_URL}/api/v1/login",
                data={"username": user["email"], "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 400:
            log.warning("langflow user login failed (%s) for %s: %s",
                        resp.status_code, user["email"], resp.text[:200])
            return None
        return (resp.json() or {}).get("access_token")
    except Exception:
        log.exception("langflow user login raised")
        return None


@launch_router.get("/launch")
async def flow_launch(
    request: Request,
    agent: str | None = Query(default=None),
    token: str | None = Query(default=None),
    user: dict | None = MaybeMe,
) -> RedirectResponse:
    """Admin-only SSO into the Langflow UI. The frontend wires this to
    AgentBuilder's "Open in Langflow" button.

    Accepts a `?token=` bearer fallback because full-tab navigations to this
    endpoint may be cross-origin (SPA on :5173, backend on :8000) and the
    `wx_session` cookie won't tag along. The SPA injects its bearer on the
    URL; we consume it once and drop it so it doesn't leak into referrers."""
    if not user and token:
        from .auth import session_user_from_token
        user = await session_user_from_token(token)
    if not user:
        raise HTTPException(401, "not authenticated")
    if (user.get("role") or "agent") not in ("admin", "owner"):
        raise HTTPException(403, "admin only")
    db = await get_db()
    lf_user_id, lf_folder_id, fresh_password = await _provision_user_and_folder(db, user)

    target_flow_id: str | None = None
    if agent:
        cur = await db.execute(
            "SELECT langflow_flow_id FROM agents WHERE id = ? AND workspace_id = ?",
            (agent, user["workspace_id"]),
        )
        row = await cur.fetchone()
        target_flow_id = (dict(row) if row else {}).get("langflow_flow_id")

    # Probe Langflow. If LANGFLOW_URL is unset OR the service isn't reachable
    # from the backend, send the operator to the dev-landing page so they see
    # instructions instead of a browser-level ERR_EMPTY_RESPONSE.
    if not LANGFLOW_URL or not await _langflow_is_up(LANGFLOW_URL):
        return RedirectResponse("/flow/dev-landing", status_code=302)

    # Grab the password we stashed during provisioning. Without it we can't
    # sign the browser in — fall back to the dev landing + surface the issue.
    password = fresh_password
    if not password:
        cur = await db.execute(
            "SELECT langflow_password_secret_id FROM users WHERE id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        if row:
            password = _decrypt(dict(row).get("langflow_password_secret_id"))
    if not password:
        return RedirectResponse("/flow/dev-landing?err=no-password", status_code=302)

    # Auto-create a blank flow on first launch for a given agent so the user
    # lands in the flow editor instead of Langflow's empty-folder "Welcome"
    # splash. We create it as superuser + assign `user_id` so the flow shows
    # up under the WorkspaceX user's account.
    if agent and not target_flow_id:
        target_flow_id = await _ensure_agent_flow(
            db, agent_id=agent, workspace_id=user["workspace_id"],
            lf_user_id=lf_user_id, lf_folder_id=lf_folder_id,
        )

    # Bridge target: go directly to the Langflow origin. We tried running LF
    # behind a reverse-proxy at `/lf/*`, but its frontend emits root-relative
    # URLs (<base href="/">) so assets 404 and API routes return 405 under a
    # prefix. The cross-origin fetch works because LF sets SameSite=Lax
    # cookies and `localhost` shares cookies across ports for same-host.
    public = (LANGFLOW_PUBLIC_URL or LANGFLOW_URL).rstrip("/")
    target_path = f"{public}/flow/{target_flow_id}" if target_flow_id else f"{public}/?folder={lf_folder_id}"
    login_path = f"{public}/api/v1/login"

    # Hand the rebrand overlay enough context to call back into WorkspaceX
    # without re-authenticating. We inject a fresh session token and the
    # WorkspaceX API origin into the redirect URL; the overlay grabs both
    # on first paint, stashes them in sessionStorage, and strips them from
    # the URL so they don't hang around in history / referer headers.
    #
    # The token reuses the caller's existing session if we can find a
    # bearer in the request — keeping cross-origin flows on one identity.
    # Otherwise we mint a fresh one via `_create_session`, scoped to the
    # same user.
    from .auth import _create_session  # avoid top-level circular on load
    auth_header = request.headers.get("authorization") or ""
    wx_token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else ""
    # `/flow/launch` accepts `?token=` too; reuse if present to keep a
    # single-session trail.
    wx_token = wx_token or (token or "")
    if not wx_token:
        try:
            wx_token = await _create_session(user["id"])
        except Exception:
            log.exception("flow_launch: could not mint session token")
            wx_token = ""
    backend_origin = str(request.base_url).rstrip("/")
    extra_q: list[str] = []
    if wx_token:
        extra_q.append(f"_wxToken={wx_token}")
    if backend_origin:
        from urllib.parse import quote
        extra_q.append(f"_wxApi={quote(backend_origin, safe='')}")
    if extra_q:
        target_path += ("&" if "?" in target_path else "?") + "&".join(extra_q)

    import html as _h
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Signing you into Langflow…</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; padding: 64px;
         max-width: 520px; margin: 0 auto; color: #e5e7eb;
         background: #0f172a; text-align: center; min-height: 100vh;
         box-sizing: border-box; }}
  .spinner {{ width: 28px; height: 28px; border: 3px solid #1f2937;
              border-top-color: #2563eb; border-radius: 50%;
              animation: spin 0.9s linear infinite; margin: 0 auto 18px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  #err {{ color: #f87171; font-size: 13px; margin-top: 16px; }}
</style>
<div class="spinner"></div>
<p>Signing you in as <b>{_h.escape(user['email'])}</b>…</p>
<div id="err"></div>
<script>
(async function () {{
  const LOGIN = {json.dumps(login_path)};
  const TARGET = {json.dumps(target_path)};
  try {{
    const body = new URLSearchParams();
    body.set("username", {json.dumps(user['email'])});
    body.set("password", {json.dumps(password)});
    const resp = await fetch(LOGIN, {{
      method: "POST",
      credentials: "include",
      headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
      body: body.toString(),
    }});
    if (!resp.ok) throw new Error("Langflow login returned " + resp.status);
    // Langflow's Set-Cookie lands on the backend origin thanks to the /lf
    // reverse-proxy — no SameSite / cross-origin gymnastics.
    window.location.replace(TARGET);
  }} catch (e) {{
    document.getElementById("err").textContent =
      "Could not sign you into Langflow (" + e.message + ")";
  }}
}})();
</script>"""
    return HTMLResponse(html)


async def _langflow_is_up(url: str) -> bool:
    """Quick 2-second health probe. Catches the "LANGFLOW_URL is set but the
    container isn't running" case so we degrade to the dev-landing page
    instead of 302'ing into a browser-level ERR_EMPTY_RESPONSE."""
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=2.0) as client:
            # Langflow serves `/health` (200 OK when ready). Fall back to `/`
            # if that route doesn't exist on older builds.
            for path in ("/health", "/"):
                try:
                    r = await client.get(f"{url.rstrip('/')}{path}")
                    if r.status_code < 500:
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


@launch_router.get("/dev-landing", response_class=HTMLResponse)
async def _dev_landing() -> HTMLResponse:
    """Target for `/flow/launch` when Langflow isn't reachable. Separates
    "never configured" from "configured but the service is down" so the
    message matches reality."""
    spa = os.environ.get("SPA_ORIGIN", "http://localhost:5173")
    back = f"{spa}/#/agents"

    if not LANGFLOW_URL:
        title = "Langflow isn't configured"
        body = f"""
<p>Set <code>LANGFLOW_URL</code> and <code>LANGFLOW_SUPERUSER_PASSWORD</code> in the
backend environment (the backend signs into Langflow as the superuser to
provision per-user accounts). In the meantime the default orchestrator
(<code>backend/app/ai/orchestrator.py</code>) drives every inbound reply;
agents created in AgentBuilder are persisted but won't have a visual editor
until LF is online.</p>"""
    else:
        title = "Langflow isn't running"
        body = f"""
<p>Your backend is pointed at <code>{LANGFLOW_URL}</code>, but nothing responded
when we probed it. Start the Langflow container and try the
<b>Create agent</b> button again:</p>
<pre><code>docker compose up -d langflow
docker logs -f workspacex-langflow-1   # wait for "Ready!"</code></pre>
<p>If Langflow is running on a different host, update
<code>LANGFLOW_URL</code> in your backend <code>.env</code> and restart the
API (<code>docker compose restart api</code>).</p>"""

    html = f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        padding: 48px; max-width: 680px; margin: 0 auto; color: #0f172a;
        line-height: 1.55; }}
code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px;
        font-size: 0.92em; }}
pre {{ background: #0f172a; color: #f8fafc; padding: 14px 18px;
       border-radius: 8px; overflow-x: auto; }}
pre code {{ background: transparent; color: inherit; padding: 0; }}
a {{ color: #2563eb; }}
</style>
<h1>{title}</h1>
{body}
<p><a href="{back}">← Back to Agent Builder</a></p>"""
    return HTMLResponse(html)


async def apply_flow_publish(
    db,
    *,
    flow_id: str,
    folder_id: str | None,
    version: int,
    data: dict | None,
    name: str | None = None,
    source: str = "webhook",
) -> dict | None:
    """Shared apply-path for Langflow publish events. Invoked by both the
    webhook and the polling fallback worker so we only have one upsert site.

    Returns `{agentId, workspaceId, version}` on success, or `None` when no
    matching workspace could be resolved.
    """
    # 1) workspace via folder → fallback to any existing agent row.
    ws_id: str | None = None
    if folder_id:
        cur = await db.execute(
            "SELECT id FROM workspaces WHERE langflow_folder_id = ? LIMIT 1",
            (folder_id,),
        )
        r = await cur.fetchone()
        if r:
            ws_id = r["id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
    if not ws_id:
        cur = await db.execute(
            "SELECT workspace_id FROM agents WHERE langflow_flow_id = ? LIMIT 1",
            (flow_id,),
        )
        r = await cur.fetchone()
        if r:
            ws_id = r["workspace_id"] if hasattr(r, "keys") else r[0]
    if not ws_id:
        return None

    # 2) Upsert the agent row. `kind` / `is_active` / `status` / `langflow_*`
    # columns are all additive ALTERs in schema.sql, so casting the UPDATE as
    # "mark as published" is safe on both fresh + existing rows.
    cur = await db.execute(
        "SELECT id FROM agents WHERE langflow_flow_id = ? AND workspace_id = ? LIMIT 1",
        (flow_id, ws_id),
    )
    existing = await cur.fetchone()
    if existing:
        agent_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        await db.execute(
            """
            UPDATE agents
               SET status = 'published', is_active = true, kind = 'langflow',
                   last_published_at = now(),
                   langflow_folder_id = COALESCE(?, langflow_folder_id),
                   name = COALESCE(?, name)
             WHERE id = ?
            """,
            (folder_id, name, agent_id),
        )
    else:
        agent_id = str(uuid4())
        await db.execute(
            """
            INSERT INTO agents (id, workspace_id, name, status, kind,
                                langflow_flow_id, langflow_folder_id, is_active,
                                last_published_at, created_at)
            VALUES (?,?,?, 'published', 'langflow', ?, ?, true, now(), now())
            """,
            (agent_id, ws_id,
             name or f"Flow {str(flow_id)[:8]}",
             flow_id, folder_id),
        )

    # 3) Snapshot row. `version` is UNIQUE per agent_id, so if the caller
    # replays the same version (e.g. webhook + poller both landing) we no-op.
    await db.execute(
        """
        INSERT INTO agent_flow_snapshots
               (id, agent_id, version, flow_json, lf_flow_id, lf_folder_id, lf_version)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT (agent_id, version) DO NOTHING
        """,
        (str(uuid4()), agent_id, version, js(data or {}),
         flow_id, folder_id, version),
    )

    # 3b) Durable snapshot in MinIO. Langflow's own DB is ephemeral in most
    # deploys, so we treat MinIO as the source of truth — the Agent Builder
    # chat playground rehydrates this back into Langflow on demand when the
    # stored `langflow_flow_id` 404s. The config blob is small (nodes + edges
    # + viewport), well under MinIO's object-size limits.
    try:
        from ..s3 import put_json as _s3_put_json
        s3_payload = {
            "flowId": str(flow_id),
            "folderId": folder_id,
            "version": version,
            "name": name,
            "data": data or {},
            "savedAt": datetime.now(timezone.utc).isoformat(),
        }
        latest_key = f"flows/{ws_id}/{agent_id}/latest.json"
        versioned_key = f"flows/{ws_id}/{agent_id}/v{version}.json"
        _s3_put_json(versioned_key, s3_payload)
        stored_key = _s3_put_json(latest_key, s3_payload)
        if stored_key:
            await db.execute(
                "UPDATE agents SET flow_s3_key = ? WHERE id = ?",
                (stored_key, agent_id),
            )
    except Exception:
        log.exception("apply_flow_publish: minio snapshot failed (non-fatal)")

    # 4) Flow-as-a-tool: keep an `apis` row pointed at this flow so the
    # orchestrator / other agents can invoke it as `run_library_api("flow:<name>")`
    # and operators see real invocation stats on the API Logs page. Row is
    # namespaced `flow:<name>` so it doesn't collide with a user-defined api.
    flow_slug = (name or f"Flow {str(flow_id)[:8]}").strip()
    api_name = f"flow:{flow_slug}"[:120]
    spec_json = js({
        "kind": "langflow",
        "flowId": str(flow_id),
        "folderId": folder_id,
        "version": version,
        "description": "Auto-created by the Langflow publish hook — invokes "
                       "the flow via /api/v1/run/<flowId>.",
    })
    cur = await db.execute(
        "SELECT id FROM apis WHERE workspace_id = ? AND name = ? LIMIT 1",
        (ws_id, api_name),
    )
    ex_api = await cur.fetchone()
    if ex_api:
        await db.execute(
            "UPDATE apis SET spec = ?, description = ? WHERE id = ?",
            (spec_json, f"Langflow flow v{version}",
             ex_api["id"] if hasattr(ex_api, "keys") else ex_api[0]),
        )
    else:
        await db.execute(
            """
            INSERT INTO apis (id, workspace_id, name, method, description, spec)
            VALUES (?,?,?,?,?,?)
            """,
            (str(uuid4()), ws_id, api_name, "POST",
             f"Langflow flow v{version}", spec_json),
        )

    await db.commit()

    await broker.publish(f"agents:{ws_id}", "flow.published",
                         {"agentId": agent_id, "flowId": str(flow_id),
                          "version": version, "source": source,
                          "at": datetime.now(timezone.utc).isoformat()})
    return {"agentId": agent_id, "workspaceId": str(ws_id), "version": version}


@launch_router.post("/save-as-agent")
async def save_as_agent(
    body: dict,
    request: Request,
    user: dict | None = MaybeMe,
) -> dict:
    """Called by the WorkspaceX rebrand overlay when the operator clicks
    Langflow's Export button. Takes whatever the browser already has — the
    current flow JSON + (optionally) the user's Langflow Global Variables —
    and persists an `agents` row, a `agent_flow_snapshots` row, and one
    `agent_secrets` row per variable (encrypted with the Fernet helper).

    Payload shape (all fields optional-except-flowId):
      {
        "flowId":      "<lf-flow-uuid>",
        "name":        "Human label",
        "description": "…",
        "data":        { nodes, edges, viewport },   // the LF flow JSON
        "version":     12,                           // optional — we'll
                                                     //  synthesize a monotonic
                                                     //  one when missing
        "variables":   [{ "name": "OPENAI_API_KEY",
                          "value": "sk-…",
                          "type":  "CREDENTIAL" }]
      }

    Authentication matches `/flow/launch`: the SPA session cookie or
    `Authorization: Bearer <wx-token>`. Fails 401 otherwise — unlike
    `/flow/api-library/*` we don't expose this via the `Internal <key>`
    path because the client is always a browser that can carry cookies.
    """
    if not user:
        # Allow the rebrand overlay to pass `?token=` the same way `/flow/launch`
        # does, so cross-origin browsers without the `wx_session` cookie still
        # authenticate cleanly.
        token = (request.query_params.get("token") or "").strip()
        if token:
            user = await session_user_from_token(token)
    if not user:
        raise HTTPException(401, "not authenticated")

    flow_id = (body.get("flowId") or body.get("flow_id") or "").strip()
    if not flow_id:
        raise HTTPException(400, "flowId is required")
    data = body.get("data") or {}
    name = (body.get("name") or "").strip() or None
    provided_version = body.get("version")

    # Read the existing snapshot count so we can synthesize a monotonic
    # version when the caller doesn't supply one. Langflow's Export dialog
    # doesn't surface a version number, so this path is the norm.
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(MAX(s.version), 0) AS v "
        "FROM agent_flow_snapshots s "
        "JOIN agents a ON a.id = s.agent_id "
        "WHERE a.langflow_flow_id = ? AND a.workspace_id = ?",
        (flow_id, user["workspace_id"]),
    )
    last = await cur.fetchone()
    next_version = int((dict(last) if last else {}).get("v") or 0) + 1
    version = int(provided_version) if provided_version else next_version

    # Read the folder id off the existing agent if we've seen this flow
    # before; otherwise fall back to the caller's workspace-level folder so
    # `apply_flow_publish` can stitch them together.
    cur = await db.execute(
        "SELECT langflow_folder_id FROM workspaces WHERE id = ? LIMIT 1",
        (user["workspace_id"],),
    )
    row = await cur.fetchone()
    folder_id = (dict(row) if row else {}).get("langflow_folder_id")

    result = await apply_flow_publish(
        db,
        flow_id=flow_id,
        folder_id=str(folder_id) if folder_id else None,
        version=version,
        data=data,
        name=name,
        source="export",
    )
    if not result:
        raise HTTPException(
            502,
            "could not resolve a workspace for this flow — either pass a "
            "folderId matching workspaces.langflow_folder_id, or make sure "
            "an agents row with this langflow_flow_id already exists.",
        )

    # Persist any API keys the caller sent. Variables land in `agent_secrets`
    # encrypted with the same Fernet helper we use for per-user Langflow
    # passwords (WORKSPACEX_SECRET_KEY-derived).
    variables = body.get("variables") or []
    stored = 0
    if isinstance(variables, list):
        for v in variables:
            if not isinstance(v, dict):
                continue
            k = (v.get("name") or v.get("key") or "").strip()
            raw = v.get("value")
            if not k or raw is None:
                continue
            try:
                ciphertext = _encrypt(str(raw))
            except Exception:
                continue
            await db.execute(
                """
                INSERT INTO agent_secrets (agent_id, key, encrypted_value)
                VALUES (?,?,?)
                ON CONFLICT (agent_id, key)
                DO UPDATE SET encrypted_value = EXCLUDED.encrypted_value,
                              updated_at = now()
                """,
                (result["agentId"], k, ciphertext),
            )
            stored += 1
    if stored:
        await db.commit()

    return {
        "ok": True,
        "agentId": result["agentId"],
        "workspaceId": result["workspaceId"],
        "version": result["version"],
        "variablesStored": stored,
    }


@webhook_router.post("/webhooks/langflow/published")
async def langflow_published(
    request: Request,
    x_langflow_signature: str | None = Header(default=None, alias="X-Langflow-Signature"),
) -> dict:
    """Langflow → FastAPI hook: `{flow_id, folder_id, version, data, published_at, user_id, name}`.

    Pipeline (shared with the polling fallback in `worker._langflow_publish_poll_tick`):

      1. HMAC-verify against `LANGFLOW_WEBHOOK_SECRET` (when configured).
      2. Match the flow to a workspace via `workspaces.langflow_folder_id`.
      3. Upsert an `agents` row (`kind='langflow'`), mark it `is_active=true`
         + `status='published'` so `select_inbound_agent` prefers it.
      4. Write a snapshot row (`agent_flow_snapshots`) so the AgentBuilder can
         render the version list + restore a prior snapshot later.
      5. Upsert a flow-as-a-tool `apis` row so other agents can invoke the flow.
      6. Publish `flow.published` on `agents:<ws>`.
    """
    raw = await request.body()
    if LANGFLOW_WEBHOOK_SECRET:
        expected = hmac.new(LANGFLOW_WEBHOOK_SECRET.encode(), raw,
                            hashlib.sha256).hexdigest()
        provided = (x_langflow_signature or "").split("=", 1)[-1].strip()
        if not provided or not hmac.compare_digest(expected, provided):
            raise HTTPException(403, "invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "invalid json")

    flow_id = payload.get("flow_id") or payload.get("id")
    if not flow_id:
        raise HTTPException(400, "flow_id required")
    folder_id = payload.get("folder_id")
    version = int(payload.get("version") or 1)
    data = payload.get("data") or {}
    name = payload.get("name")

    db = await get_db()
    result = await apply_flow_publish(
        db,
        flow_id=str(flow_id),
        folder_id=str(folder_id) if folder_id else None,
        version=version,
        data=data,
        name=name,
        source="webhook",
    )
    if not result:
        return {"ok": True, "ignored": "no workspace match"}
    return {"ok": True, **result}
