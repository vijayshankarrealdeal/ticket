"""Telegram Bot API adapter (spec §6).

Inbound path: a polling loop hits `getUpdates` every 10 s per connected bot
and funnels each message through `ingest_inbound_message`. Same fan-out as
WhatsApp/Gmail — the intent classifier, AI auto-reply and WS bridge don't
care which channel the message came from.

We prefer polling over webhooks because localhost dev doesn't have a public
HTTPS URL Telegram could push to. When the admin saves a `webhook_secret` in
the connector modal we *also* register `setWebhook`, and the polling loop
skips that bot (webhook mode takes precedence).

Outbound: `_send_telegram` in worker.py posts to `/sendMessage` with the
stored bot token.
"""
from __future__ import annotations

import logging

from ..db import get_db, jl, js

log = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}"


async def _http_get(url: str, params: dict | None = None) -> tuple[int, dict | str]:
    try:
        import httpx  # type: ignore
    except ImportError:
        return 0, "httpx not installed"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params or {})
    except Exception as e:
        return 0, repr(e)
    try:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    except Exception:
        body = resp.text
    return resp.status_code, body


async def _http_post(url: str, json_body: dict) -> tuple[int, dict | str]:
    try:
        import httpx  # type: ignore
    except ImportError:
        return 0, "httpx not installed"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=json_body)
    except Exception as e:
        return 0, repr(e)
    try:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    except Exception:
        body = resp.text
    return resp.status_code, body


async def register_webhook_if_configured(
    *, bot_token: str | None, webhook_base: str | None,
    webhook_secret: str | None,
) -> dict:
    """If the admin opted into webhooks (`webhook_secret` present) and we have
    a public base URL, point the bot at `{webhook_base}/webhooks/telegram`."""
    if not bot_token or not webhook_base or not webhook_secret:
        return {"skipped": True}
    url = f"{webhook_base.rstrip('/')}/webhooks/telegram"
    status, body = await _http_post(
        TG_API.format(token=bot_token) + "/setWebhook",
        {"url": url, "secret_token": webhook_secret,
         "allowed_updates": ["message", "edited_message", "callback_query"]},
    )
    return {"status": status, "body": body}


async def poll_telegram_for_credential(db, cred_row: dict) -> dict:
    """Pull new updates for one bot credential. Uses the stored
    `meta.last_update_id` as a cursor so we don't re-ingest messages the
    bot has already seen."""
    from ..routers.conversations import (
        ingest_inbound_message,
        resolve_or_create_conversation,
        resolve_or_create_customer,
    )

    secrets = jl(cred_row.get("secrets"), {}) or {}
    meta = jl(cred_row.get("meta"), {}) or {}
    token = secrets.get("bot_token")
    ws_id = cred_row["workspace_id"]
    cred_id = cred_row["id"]
    if not token:
        return {"ingested": 0, "fetched": 0, "error": "missing bot_token"}
    # Skip if webhook is configured — we'd otherwise double-ingest.
    if meta.get("use_webhook") or meta.get("webhook_secret"):
        return {"ingested": 0, "fetched": 0, "note": "webhook-mode"}

    offset = int(meta.get("last_update_id") or 0)
    status, body = await _http_get(
        TG_API.format(token=token) + "/getUpdates",
        {"offset": offset + 1 if offset else None,
         "timeout": 0, "limit": 50,
         "allowed_updates": "message,edited_message"},
    )
    if status != 200 or not isinstance(body, dict) or not body.get("ok"):
        return {"ingested": 0, "fetched": 0, "error": f"list {status}: {str(body)[:200]}"}

    updates = body.get("result") or []
    ingested = 0
    last_seen = offset

    for upd in updates:
        uid = int(upd.get("update_id") or 0)
        if uid > last_seen:
            last_seen = uid
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            continue
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        name = (
            (sender.get("first_name") or "") + " " + (sender.get("last_name") or "")
        ).strip() or (sender.get("username") or f"TG {chat.get('id')}")
        external_id = f"tg_{chat.get('id')}_{msg.get('message_id')}"

        try:
            customer_id = await resolve_or_create_customer(
                workspace_id=ws_id,
                psid=str(chat.get("id")) if chat.get("id") else None,
                name=name,
            )
            conv_id = await resolve_or_create_conversation(
                workspace_id=ws_id, customer_id=customer_id, channel="telegram",
                external_thread_id=str(chat.get("id")),
            )
            await ingest_inbound_message(
                workspace_id=ws_id, conversation_id=conv_id, text=text,
                from_name=name, external_message_id=external_id,
            )
            ingested += 1
        except Exception:
            log.exception("telegram ingest failed (update=%s)", uid)
            continue

    # Persist the cursor so the next tick doesn't re-poll old messages.
    if last_seen > offset:
        meta["last_update_id"] = last_seen
        await db.execute(
            "UPDATE platform_credentials SET meta = ? WHERE id = ?",
            (js(meta), cred_id),
        )
        await db.commit()

    return {"ingested": ingested, "fetched": len(updates)}


async def poll_all_telegram_bots(workspace_id: str | None = None) -> dict:
    db = await get_db()
    clauses = ["provider = 'telegram'", "status = 'active'"]
    params: list = []
    if workspace_id:
        clauses.append("workspace_id = ?"); params.append(workspace_id)
    sql = f"SELECT * FROM platform_credentials WHERE {' AND '.join(clauses)} ORDER BY created_at"
    cur = await db.execute(sql, tuple(params))
    rows = [dict(r) for r in await cur.fetchall()]
    totals = {"accounts": 0, "ingested": 0, "fetched": 0, "errors": []}
    for r in rows:
        result = await poll_telegram_for_credential(db, r)
        totals["accounts"] += 1
        totals["ingested"] += int(result.get("ingested") or 0)
        totals["fetched"] += int(result.get("fetched") or 0)
        if result.get("error"):
            totals["errors"].append({"account": r.get("account_id"),
                                     "error": result["error"]})
    return totals
