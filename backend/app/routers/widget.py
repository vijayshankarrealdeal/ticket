"""Web widget (spec §6.5).

The embedded chat widget on a customer's marketing site connects here. It's
unauthenticated — the workspace is identified by the slug in the query
string, and the visitor by a stable client-generated `visitorId`. We bootstrap
a `customers.kind='visitor'` row and a `conversations` thread on first
contact, then route messages through the same `POST /conversations/:id/inbound`
pipeline so the AI auto-reply / intent classifier / WS fan-out path is
identical to WhatsApp / Email.

Endpoints:
- `GET  /widget/bootstrap?workspace=<slug>&visitor=<id>` — returns
  `{conversationId, customerId}`. Idempotent — safe to call on every page load.
- `POST /widget/messages` — `{conversationId, text}` from the visitor.
- `WS   /widget/ws?workspace=<slug>&visitor=<id>` — server pushes
  `message.created` envelopes for that conversation. Subscribe is implicit
  (the conversation id is resolved server-side).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response

from ..db import get_db, jl
from ..ws import broker

router = APIRouter(tags=["widget"])

WIDGET_CHANNEL = "widget"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _resolve_workspace(slug: str) -> str | None:
    db = await get_db()
    cur = await db.execute("SELECT id FROM workspaces WHERE slug = ?", (slug,))
    r = await cur.fetchone()
    return r[0] if r else None


async def _bootstrap(slug: str, visitor_id: str, ua: str | None = None) -> dict:
    if not slug or not visitor_id:
        raise HTTPException(400, "workspace + visitor required")
    ws_id = await _resolve_workspace(slug)
    if not ws_id:
        raise HTTPException(404, "workspace not found")
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, customer_id, conversation_id FROM widget_visitors
         WHERE workspace_id = ? AND visitor_id = ?
        """,
        (ws_id, visitor_id),
    )
    existing = await cur.fetchone()
    if existing:
        e = dict(existing)
        await db.execute(
            "UPDATE widget_visitors SET last_seen_at = now() WHERE id = ?", (e["id"],)
        )
        await db.commit()
        return {
            "workspaceId": ws_id,
            "visitorRowId": e["id"],
            "customerId": e["customer_id"],
            "conversationId": e["conversation_id"],
        }
    # First-time visitor — create a `visitor` customer + a conversation row.
    cust_id = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO customers (id, workspace_id, name, kind)
        VALUES (?, ?, ?, 'visitor')
        """,
        (cust_id, ws_id, f"Visitor {visitor_id[:6]}"),
    )
    await db.execute(
        """
        INSERT INTO conversations (id, workspace_id, customer_id, channel, status, preview)
        VALUES (?, ?, ?, ?, 'open', 'Web visitor opened the chat widget')
        """,
        (conv_id, ws_id, cust_id, WIDGET_CHANNEL),
    )
    await db.execute(
        """
        INSERT INTO widget_visitors (id, workspace_id, visitor_id, customer_id,
                                     conversation_id, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (row_id, ws_id, visitor_id, cust_id, conv_id, (ua or "")[:500]),
    )
    await db.commit()
    await broker.publish(
        f"inbox:{ws_id}", "conversation.updated",
        {"id": conv_id, "channel": WIDGET_CHANNEL,
         "preview": "Web visitor opened the chat widget", "at": _now_iso()},
    )
    return {
        "workspaceId": ws_id, "visitorRowId": row_id,
        "customerId": cust_id, "conversationId": conv_id,
    }


@router.get("/widget/bootstrap")
async def widget_bootstrap(workspace: str = Query(...), visitor: str = Query(...)) -> dict:
    return await _bootstrap(workspace, visitor)


@router.post("/widget/messages")
async def widget_post(body: dict) -> dict:
    """Visitor → workspace. Persists the inbound, fans out on `conv:<id>` /
    `inbox:<ws>`, and kicks off the AI auto-reply via the same orchestrator
    the authed inbound endpoint uses."""
    import asyncio as _aio
    cid = (body or {}).get("conversationId")
    text = ((body or {}).get("text") or "").strip()
    if not cid or not text:
        raise HTTPException(400, "conversationId + text required")
    db = await get_db()
    cur = await db.execute(
        "SELECT id, workspace_id, customer_id, channel FROM conversations WHERE id = ?", (cid,)
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "conversation not found")
    conv = dict(row)
    msg_id = str(uuid.uuid4())
    now = _now_iso()
    preview = text[:160]
    from_name = (body or {}).get("from") or "Visitor"
    await db.execute(
        """
        INSERT INTO messages (id, workspace_id, conversation_id, from_role, from_name,
                              at, text, attachments)
        VALUES (?,?,?, 'customer', ?, ?, ?, ?)
        """,
        (msg_id, conv["workspace_id"], cid, from_name, now, text, "[]"),
    )
    await db.execute(
        """
        UPDATE conversations
           SET preview = ?, last_message_at = ?, last_inbound_at = ?,
               unread_count = unread_count + 1
         WHERE id = ?
        """,
        (preview, now, now, cid),
    )
    await db.commit()
    msg = {"id": msg_id, "from": "them", "fromName": from_name, "at": now,
           "text": text, "delivered": True, "attachments": []}
    await broker.publish(f"conv:{cid}", "message.created", msg)
    await broker.publish(
        f"inbox:{conv['workspace_id']}", "conversation.updated",
        {"id": cid, "preview": preview, "channel": WIDGET_CHANNEL, "at": now},
    )
    # Fire-and-forget AI auto-reply through the same orchestrator path.
    from .conversations import _run_inbound_ai_reply  # local import avoids cycles
    _aio.create_task(_run_inbound_ai_reply(
        workspace_id=conv["workspace_id"], conversation_id=cid,
        channel=conv.get("channel") or WIDGET_CHANNEL,
        customer_id=conv.get("customer_id"),
        actor_user_id=None, prompt=text,
    ))
    return {"message": msg}


@router.get("/widget.js")
async def widget_script(request: Request, workspace: str = Query(...)) -> Response:
    """Drop-in embed script. Customer pastes a single `<script>` tag onto
    their site and a launcher + chat panel mount in the bottom-right. The
    script connects to `wss:/widget/ws` for realtime replies and posts to
    `/widget/messages` for inbound. Proactive prompts fire after 20 s of
    mouse idle (configurable via `data-proactive-ms`)."""
    base = str(request.base_url).rstrip("/")
    script = _EMBED_JS_TEMPLATE.replace("__BASE__", base).replace("__WORKSPACE__", workspace)
    return Response(
        content=script,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=60"},
    )


_EMBED_JS_TEMPLATE = r"""
(function () {
  if (window.__WorkspaceXWidget) return;
  window.__WorkspaceXWidget = true;
  var BASE = "__BASE__";
  var WS = "__WORKSPACE__";

  // Stable per-browser visitor id.
  var visitor = localStorage.getItem("wsx.visitor");
  if (!visitor) {
    visitor = "v_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("wsx.visitor", visitor);
  }

  var launcher = document.createElement("button");
  launcher.setAttribute("aria-label", "Chat with support");
  launcher.style.cssText =
    "position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:999px;" +
    "background:#2563eb;color:#fff;font-size:22px;border:none;cursor:pointer;" +
    "box-shadow:0 10px 25px rgba(0,0,0,.2);z-index:2147483646;";
  launcher.textContent = "💬";
  document.body.appendChild(launcher);

  var panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;right:20px;bottom:88px;width:340px;max-height:520px;display:none;" +
    "background:#fff;color:#0f172a;border-radius:14px;box-shadow:0 20px 50px rgba(0,0,0,.25);" +
    "overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;z-index:2147483647;";
  panel.innerHTML =
    '<div id="wsx-head" style="background:#1e40af;color:#fff;padding:12px 14px;font-weight:600;">' +
    'Support' +
    '<span id="wsx-sub" style="display:block;font-weight:400;font-size:12px;opacity:.8;">Typically replies in minutes</span>' +
    '</div>' +
    '<div id="wsx-msgs" style="padding:12px;height:340px;overflow:auto;background:#f8fafc;font-size:14px;"></div>' +
    '<form id="wsx-form" style="display:flex;gap:8px;padding:10px;border-top:1px solid #e2e8f0;background:#fff;">' +
    '<input id="wsx-in" placeholder="Type a message…" style="flex:1;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:14px;outline:none;" />' +
    '<button style="background:#2563eb;color:#fff;border:none;padding:8px 14px;border-radius:8px;cursor:pointer;font-weight:600;">Send</button>' +
    '</form>';
  document.body.appendChild(panel);

  function open() {
    panel.style.display = "block";
    launcher.style.display = "none";
    boot();
    input.focus();
  }
  function close() { panel.style.display = "none"; launcher.style.display = "block"; }

  launcher.addEventListener("click", open);
  var head = panel.querySelector("#wsx-head");
  head.addEventListener("dblclick", close);

  var input = panel.querySelector("#wsx-in");
  var msgs = panel.querySelector("#wsx-msgs");
  var form = panel.querySelector("#wsx-form");
  var booted = null;
  var ws = null;
  var seen = new Set();

  function bubble(from, text, when) {
    var el = document.createElement("div");
    var mine = from === "me";
    el.style.cssText =
      "margin:6px 0;max-width:80%;padding:8px 12px;border-radius:12px;line-height:1.35;" +
      (mine
        ? "background:#2563eb;color:#fff;margin-left:auto;border-bottom-right-radius:4px;"
        : "background:#fff;border:1px solid #e2e8f0;margin-right:auto;border-bottom-left-radius:4px;");
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function boot() {
    if (booted) return booted;
    try {
      var res = await fetch(BASE + "/widget/bootstrap?workspace=" + encodeURIComponent(WS) +
                            "&visitor=" + encodeURIComponent(visitor));
      booted = await res.json();
      openSocket();
      bubble("them", "👋 Hi — how can we help today?", null);
      return booted;
    } catch (e) {
      bubble("them", "Couldn't reach support. Please try again in a minute.", null);
    }
  }

  function openSocket() {
    var url = BASE.replace(/^http/, "ws") + "/widget/ws?workspace=" +
              encodeURIComponent(WS) + "&visitor=" + encodeURIComponent(visitor);
    ws = new WebSocket(url);
    ws.onmessage = function (e) {
      try {
        var env = JSON.parse(e.data);
        if (env.type === "message.created" && env.data) {
          if (seen.has(env.data.id)) return;
          seen.add(env.data.id);
          var from = env.data.from === "me" ? "them" : "them"; // server 'me' = agent
          if (env.data.from === "them") return; // skip visitor echoes
          bubble("them", env.data.text || "", env.data.at);
        }
      } catch (err) { /* ignore */ }
    };
    ws.onclose = function () { setTimeout(openSocket, 2500); };
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text || !booted) return;
    bubble("me", text);
    input.value = "";
    fetch(BASE + "/widget/messages", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId: booted.conversationId, text: text }),
    }).catch(function () { bubble("them", "(failed to send — please retry)"); });
  });

  // Proactive prompt: open after the configured idle period on the first visit.
  var proactiveMs = parseInt((document.currentScript || {}).dataset?.proactiveMs || "0", 10);
  if (!proactiveMs) {
    var tag = document.querySelector('script[data-proactive-ms]');
    if (tag) proactiveMs = parseInt(tag.dataset.proactiveMs, 10);
  }
  if (proactiveMs > 0 && !sessionStorage.getItem("wsx.proactive")) {
    setTimeout(function () {
      if (panel.style.display === "none") {
        sessionStorage.setItem("wsx.proactive", "1");
        open();
      }
    }, proactiveMs);
  }
})();
"""


@router.websocket("/widget/ws")
async def widget_ws(ws: WebSocket, workspace: str = Query(...), visitor: str = Query(...)) -> None:
    """Stream `message.created` envelopes for the visitor's conversation. We
    transparently subscribe to `conv:<id>` so the agent's reply lands in the
    widget the moment it's persisted."""
    try:
        boot = await _bootstrap(workspace, visitor, ws.headers.get("user-agent"))
    except HTTPException as e:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason=e.detail[:100])
        return
    await ws.accept()
    topic = f"conv:{boot['conversationId']}"
    await broker.subscribe(ws, [topic])
    try:
        await ws.send_text(json.dumps({"type": "hello", "data": boot}, default=str))
        while True:
            raw = await ws.receive_text()
            # Visitors can send messages over the same socket if they prefer.
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("action") == "message" and msg.get("text"):
                await widget_post({"conversationId": boot["conversationId"],
                                   "text": msg["text"], "from": "Visitor"})
            elif msg.get("action") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await broker.unsubscribe(ws, [topic])
