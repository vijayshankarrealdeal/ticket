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

log = logging.getLogger(__name__)

launch_router = APIRouter(prefix="/flow", tags=["langflow"])
webhook_router = APIRouter(tags=["langflow-webhooks"])


LANGFLOW_URL = os.environ.get("LANGFLOW_URL")                 # e.g. http://langflow:7860
LANGFLOW_ADMIN_TOKEN = os.environ.get("LANGFLOW_ADMIN_TOKEN")  # superuser bearer
LANGFLOW_WEBHOOK_SECRET = os.environ.get("LANGFLOW_WEBHOOK_SECRET")


async def _provision_user_and_folder(db, user: dict) -> tuple[str, str]:
    """Ensure the Langflow user + per-workspace folder exist, returning
    `(langflow_user_id, langflow_folder_id)`. The first call for each user
    creates an LF account named after their email; subsequent calls reuse
    the stored ids. Dev-mode synthesises opaque UUIDs so the UI round-trips
    without LF running."""
    lf_user_id = user.get("langflow_user_id")
    lf_folder_id = None
    cur = await db.execute(
        "SELECT langflow_folder_id FROM workspaces WHERE id = ?", (user["workspace_id"],)
    )
    w = await cur.fetchone()
    if w:
        lf_folder_id = dict(w).get("langflow_folder_id")

    if lf_user_id and lf_folder_id:
        return str(lf_user_id), str(lf_folder_id)

    if not LANGFLOW_URL or not LANGFLOW_ADMIN_TOKEN:
        # Dev-mode provisioning — synthetic ids so the schema has something real.
        lf_user_id = lf_user_id or str(uuid4())
        lf_folder_id = lf_folder_id or str(uuid4())
    else:
        try:
            import httpx  # type: ignore
            async with httpx.AsyncClient(timeout=15) as client:
                if not lf_user_id:
                    r = await client.post(
                        f"{LANGFLOW_URL}/api/v1/users/",
                        headers={"Authorization": f"Bearer {LANGFLOW_ADMIN_TOKEN}"},
                        json={"username": user["email"], "password": uuid4().hex,
                              "is_active": True},
                    )
                    if r.status_code < 400:
                        lf_user_id = (r.json() or {}).get("id") or str(uuid4())
                    else:
                        # Conflict (user already exists) — look them up.
                        list_r = await client.get(
                            f"{LANGFLOW_URL}/api/v1/users/",
                            headers={"Authorization": f"Bearer {LANGFLOW_ADMIN_TOKEN}"},
                            params={"username": user["email"]},
                        )
                        if list_r.status_code < 400:
                            entries = list_r.json() or []
                            if entries:
                                lf_user_id = entries[0].get("id")
                    lf_user_id = lf_user_id or str(uuid4())
                if not lf_folder_id:
                    f = await client.post(
                        f"{LANGFLOW_URL}/api/v1/folders/",
                        headers={"Authorization": f"Bearer {LANGFLOW_ADMIN_TOKEN}"},
                        json={"name": f"ws-{user['workspace_id']}",
                              "description": f"Workspace {user['workspace_id']}"},
                    )
                    if f.status_code < 400:
                        lf_folder_id = (f.json() or {}).get("id") or str(uuid4())
                    lf_folder_id = lf_folder_id or str(uuid4())
        except Exception:
            log.exception("langflow provisioning failed")
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
    return str(lf_user_id), str(lf_folder_id)


async def _mint_access_token(lf_user_id: str) -> str | None:
    """Exchange the admin token for a user-scoped access token so the browser
    doesn't see the superuser credentials. Returns the JWT when LF is
    configured, None in dev mode."""
    if not LANGFLOW_URL or not LANGFLOW_ADMIN_TOKEN:
        return None
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{LANGFLOW_URL}/api/v1/impersonate",
                headers={"Authorization": f"Bearer {LANGFLOW_ADMIN_TOKEN}"},
                json={"user_id": lf_user_id},
            )
        if resp.status_code >= 400:
            return None
        return (resp.json() or {}).get("access_token")
    except Exception:
        log.exception("langflow impersonate failed")
        return None


@launch_router.get("/launch")
async def flow_launch(
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
    lf_user_id, lf_folder_id = await _provision_user_and_folder(db, user)

    target_flow_id: str | None = None
    if agent:
        cur = await db.execute(
            "SELECT langflow_flow_id FROM agents WHERE id = ? AND workspace_id = ?",
            (agent, user["workspace_id"]),
        )
        row = await cur.fetchone()
        target_flow_id = (dict(row) if row else {}).get("langflow_flow_id")

    access_token = await _mint_access_token(lf_user_id)

    # Pick the redirect target. If LANGFLOW_URL is unset (dev) OR the service
    # isn't actually reachable, send the operator to the dev-landing page so
    # they see instructions instead of a browser-level ERR_EMPTY_RESPONSE.
    base = LANGFLOW_URL
    if base and not await _langflow_is_up(base):
        base = None

    if not base:
        return RedirectResponse("/flow/dev-landing", status_code=302)

    if target_flow_id:
        url = f"{base.rstrip('/')}/flow/{target_flow_id}"
    else:
        url = f"{base.rstrip('/')}/?folder={lf_folder_id}"

    resp = RedirectResponse(url, status_code=302)
    if access_token:
        resp.set_cookie(
            "access_token_lf", access_token,
            httponly=True, samesite="lax",
            max_age=60 * 60 * 8,  # 8h — matches LF default session.
        )
    return resp


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
<p>Set <code>LANGFLOW_URL</code> and <code>LANGFLOW_ADMIN_TOKEN</code> in the backend
environment to enable the visual flow editor. In the meantime the default
orchestrator (<code>backend/app/ai/orchestrator.py</code>) drives every inbound
reply; agents created in AgentBuilder are persisted but won't have a visual
editor until LF is online.</p>"""
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


@webhook_router.post("/webhooks/langflow/published")
async def langflow_published(
    request: Request,
    x_langflow_signature: str | None = Header(default=None, alias="X-Langflow-Signature"),
) -> dict:
    """Langflow → FastAPI hook: `{flow_id, folder_id, version, data, published_at, user_id}`.

    We:
      1. HMAC-verify against `LANGFLOW_WEBHOOK_SECRET` (when configured).
      2. Match the flow to a workspace via `workspaces.langflow_folder_id`.
      3. Upsert an `agents` row (`kind='langflow'`), mark it `is_active=true`
         + `status='published'` so `select_inbound_agent` prefers it.
      4. Write a snapshot row (`agent_flow_snapshots`) so the AgentBuilder can
         render the version list + restore a prior snapshot later.
      5. Publish `flow.published` on `agents:<ws>` so any open AgentBuilder
         tab refreshes without a polling loop.
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
    folder_id = payload.get("folder_id")
    version = int(payload.get("version") or 1)
    data = payload.get("data") or {}
    if not flow_id:
        raise HTTPException(400, "flow_id required")

    db = await get_db()
    # 1) workspace via folder → fallback to any existing agent row.
    ws_id = None
    if folder_id:
        cur = await db.execute(
            "SELECT id FROM workspaces WHERE langflow_folder_id = ? LIMIT 1",
            (folder_id,),
        )
        r = await cur.fetchone()
        if r:
            ws_id = r[0]
    if not ws_id:
        cur = await db.execute(
            "SELECT workspace_id FROM agents WHERE langflow_flow_id = ? LIMIT 1",
            (flow_id,),
        )
        r = await cur.fetchone()
        if r:
            ws_id = r[0]
    if not ws_id:
        return {"ok": True, "ignored": "no workspace match"}

    # 2) Upsert the agent row.
    cur = await db.execute(
        "SELECT id FROM agents WHERE langflow_flow_id = ? AND workspace_id = ?",
        (flow_id, ws_id),
    )
    existing = await cur.fetchone()
    if existing:
        agent_id = existing["id"]
        await db.execute(
            """
            UPDATE agents
               SET status = 'published', is_active = true, kind = 'langflow',
                   updated_at = now(), langflow_folder_id = COALESCE(?, langflow_folder_id)
             WHERE id = ?
            """,
            (folder_id, agent_id),
        )
    else:
        agent_id = str(uuid4())
        await db.execute(
            """
            INSERT INTO agents (id, workspace_id, name, status, kind,
                                langflow_flow_id, langflow_folder_id, is_active,
                                created_at, updated_at)
            VALUES (?,?,?, 'published', 'langflow', ?, ?, true, now(), now())
            """,
            (agent_id, ws_id,
             payload.get("name") or f"Flow {str(flow_id)[:8]}",
             flow_id, folder_id),
        )

    # 3) Snapshot row.
    await db.execute(
        """
        INSERT INTO agent_flow_snapshots (id, agent_id, workspace_id, data,
                                          lf_flow_id, lf_folder_id, lf_version,
                                          created_at)
        VALUES (?,?,?,?,?,?,?, now())
        """,
        (str(uuid4()), agent_id, ws_id, js(data),
         flow_id, folder_id, version),
    )
    await db.commit()

    await broker.publish(f"agents:{ws_id}", "flow.published",
                         {"agentId": agent_id, "flowId": flow_id, "version": version,
                          "at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "agentId": agent_id, "version": version}
