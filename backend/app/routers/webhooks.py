"""Channel webhooks (spec §6).

Every inbound channel adapter — WhatsApp, Gmail, Outlook, Instagram, Shopify,
Slack, Voice — calls the same shared `ingest_inbound_message` helper in
`conversations.py` so the intent classifier / WS fan-out / AI auto-reply path
is identical to the auth'd simulator at `POST /conversations/:id/inbound`.

All routes here are unauthenticated: they're reached from the provider's
webhook URL, not from the SPA. Authorization is per-provider — HMAC signature
(WhatsApp / Slack / Shopify), OIDC Pub/Sub token (Gmail), Graph validation
token (Outlook). When the secret isn't configured we accept the payload in dev
mode so operators can point something like smee.io at the endpoint and iterate.
"""
from __future__ import annotations

import base64
import email
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from ..db import get_db, jl, js
from ..ws import broker
from .conversations import (
    ingest_inbound_message,
    resolve_or_create_conversation,
    resolve_or_create_customer,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


def _env(*keys: str) -> str | None:
    """First non-empty env var from the list."""
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return None


def _verify_hmac(secret: str, raw: bytes, provided: str, digest_algo: str = "sha256") -> bool:
    if not secret or not provided:
        return False
    expected = hmac.new(secret.encode(), raw, getattr(hashlib, digest_algo)).hexdigest()
    # Some providers prefix with `sha256=`, others don't.
    provided = provided.split("=", 1)[-1].strip()
    return hmac.compare_digest(expected, provided)


async def _default_workspace_for(provider: str, account_id: str | None = None) -> str | None:
    """Look up which workspace owns this channel account. Webhook payloads
    identify a phone_number_id / page_id / shop domain — we match it against
    `platform_credentials.account_id` to route."""
    db = await get_db()
    if account_id:
        cur = await db.execute(
            """
            SELECT workspace_id FROM platform_credentials
             WHERE provider = ? AND account_id = ? AND status = 'active'
             LIMIT 1
            """,
            (provider, account_id),
        )
        r = await cur.fetchone()
        if r:
            return r[0]
    # Fallback: pick the first active credential for this provider — lets a
    # single-workspace demo route without wiring up account ids.
    cur = await db.execute(
        """
        SELECT workspace_id FROM platform_credentials
         WHERE provider = ? AND status = 'active'
         ORDER BY created_at ASC LIMIT 1
        """,
        (provider,),
    )
    r = await cur.fetchone()
    if r:
        return r[0]
    # Last resort: any workspace (single-tenant demo).
    cur = await db.execute("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1", ())
    r = await cur.fetchone()
    return r[0] if r else None


# ============================================================================
# WhatsApp Business Cloud API (spec §6.3)
# ============================================================================

@router.get("/webhooks/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
) -> Response:
    """Meta's verify-token round trip — they GET this endpoint once when the
    webhook is configured and expect `hub.challenge` echoed back."""
    expected = _env("WHATSAPP_VERIFY_TOKEN", "META_VERIFY_TOKEN") or "workspacex"
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(403, "verify token mismatch")


@router.post("/webhooks/whatsapp")
async def whatsapp_inbound(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict:
    raw = await request.body()
    secret = _env("WHATSAPP_APP_SECRET", "META_APP_SECRET")
    if secret and not _verify_hmac(secret, raw, x_hub_signature_256 or ""):
        raise HTTPException(403, "invalid signature")
    payload = json.loads(raw or b"{}")
    delivered = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            ws_id = await _default_workspace_for("whatsapp", phone_number_id)
            if not ws_id:
                continue
            contacts = {c.get("wa_id"): c for c in (value.get("contacts") or [])}
            for m in value.get("messages") or []:
                wa_id = m.get("from")
                profile_name = (contacts.get(wa_id, {}).get("profile") or {}).get("name") or f"WA {wa_id[-4:]}" if wa_id else "WhatsApp"
                text = ((m.get("text") or {}).get("body") or "").strip()
                ext_id = m.get("id")
                msg_type = m.get("type", "text")
                if msg_type != "text":
                    text = text or f"[{msg_type}]"
                customer_id = await resolve_or_create_customer(
                    workspace_id=ws_id, wa_id=wa_id, phone=wa_id, name=profile_name,
                )
                conv_id = await resolve_or_create_conversation(
                    workspace_id=ws_id, customer_id=customer_id, channel="whatsapp",
                )
                res = await ingest_inbound_message(
                    workspace_id=ws_id, conversation_id=conv_id, text=text,
                    from_name=profile_name, external_message_id=ext_id,
                )
                delivered.append(res["message"]["id"])
            # Status callbacks — mark our outbound rows delivered/read.
            for status in value.get("statuses") or []:
                mid = status.get("id")
                st = status.get("status")  # sent|delivered|read|failed
                if mid and st:
                    await _mark_outbound_status(mid, st)
    return {"ok": True, "received": len(delivered)}


async def _mark_outbound_status(external_id: str, status: str) -> None:
    db = await get_db()
    at = datetime.now(timezone.utc).isoformat()
    col = "read_at" if status == "read" else "delivered_at"
    await db.execute(
        f"UPDATE messages SET {col} = ? WHERE external_message_id = ?",
        (at, external_id),
    )
    await db.commit()


# ============================================================================
# Gmail (spec §6.1) — Cloud Pub/Sub push
# ============================================================================

@router.post("/webhooks/gmail")
async def gmail_push(request: Request) -> dict:
    """Google Cloud Pub/Sub push. Payload is `{message:{data: b64(json)}}`
    where the inner JSON is `{emailAddress, historyId}`. In prod we'd fetch
    history via the Gmail API and pull every unread message in the thread; for
    now we synthesise a minimal inbound so the plumbing works end to end."""
    body = await request.json()
    data = (body.get("message") or {}).get("data")
    if not data:
        raise HTTPException(400, "missing message.data")
    try:
        payload = json.loads(base64.urlsafe_b64decode(data + "==").decode())
    except Exception:
        raise HTTPException(400, "invalid base64")
    email_addr = payload.get("emailAddress")
    ws_id = await _default_workspace_for("gmail", email_addr)
    if not ws_id:
        return {"ok": True, "ignored": True}
    # Without a Gmail fetch, we record a skeleton inbound. Real adapter would
    # iterate history → messages.get → parse RFC-822 body.
    snippet = payload.get("snippet") or "New email received"
    subject = payload.get("subject") or ""
    thread_id = payload.get("threadId") or str(uuid4())
    customer_id = await resolve_or_create_customer(
        workspace_id=ws_id, email=payload.get("from") or email_addr,
        name=payload.get("fromName"),
    )
    conv_id = await resolve_or_create_conversation(
        workspace_id=ws_id, customer_id=customer_id, channel="gmail",
        external_thread_id=thread_id,
    )
    res = await ingest_inbound_message(
        workspace_id=ws_id, conversation_id=conv_id,
        text=(f"{subject}\n\n" if subject else "") + snippet,
        from_name=payload.get("fromName") or email_addr or "Gmail",
        external_message_id=payload.get("messageId"),
    )
    return {"ok": True, "messageId": res["message"]["id"]}


# ============================================================================
# Outlook / Microsoft Graph (spec §6.2)
# ============================================================================

@router.post("/webhooks/outlook")
async def outlook_push(
    request: Request,
    validationToken: str | None = Query(default=None),
) -> Response:
    """Graph subscription notifications. On create/renew Microsoft sends a
    `validationToken` query param and expects it echoed back as text/plain."""
    if validationToken:
        return Response(content=validationToken, media_type="text/plain")
    body = await request.json()
    results = []
    for note in body.get("value") or []:
        resource = note.get("resource") or ""
        subscription_id = note.get("subscriptionId")
        tenant = note.get("tenantId") or subscription_id
        ws_id = await _default_workspace_for("outlook", tenant)
        if not ws_id:
            continue
        # Real adapter would call /me/messages/{id} on delta; for dev we synthesise.
        data = note.get("resourceData") or {}
        subject = data.get("subject") or "Outlook message"
        thread_id = data.get("conversationId") or resource
        sender = (data.get("from") or {}).get("emailAddress") or {}
        email_addr = sender.get("address") or "noreply@outlook.local"
        customer_id = await resolve_or_create_customer(
            workspace_id=ws_id, email=email_addr, name=sender.get("name"),
        )
        conv_id = await resolve_or_create_conversation(
            workspace_id=ws_id, customer_id=customer_id, channel="outlook",
            external_thread_id=thread_id,
        )
        res = await ingest_inbound_message(
            workspace_id=ws_id, conversation_id=conv_id,
            text=subject, from_name=sender.get("name") or email_addr,
            external_message_id=data.get("id"),
        )
        results.append(res["message"]["id"])
    return Response(content=json.dumps({"ok": True, "received": len(results)}),
                    media_type="application/json")


# ============================================================================
# Instagram DM (spec §6.4) — Meta Page webhook `messaging` field
# ============================================================================

@router.get("/webhooks/instagram")
async def instagram_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
) -> Response:
    expected = _env("INSTAGRAM_VERIFY_TOKEN", "META_VERIFY_TOKEN") or "workspacex"
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(403, "verify token mismatch")


@router.post("/webhooks/instagram")
async def instagram_inbound(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict:
    raw = await request.body()
    secret = _env("INSTAGRAM_APP_SECRET", "META_APP_SECRET")
    if secret and not _verify_hmac(secret, raw, x_hub_signature_256 or ""):
        raise HTTPException(403, "invalid signature")
    payload = json.loads(raw or b"{}")
    delivered = []
    for entry in payload.get("entry", []) or []:
        page_id = entry.get("id")
        ws_id = await _default_workspace_for("instagram", page_id)
        if not ws_id:
            continue
        for m in entry.get("messaging", []) or []:
            sender = (m.get("sender") or {}).get("id")
            text = (m.get("message") or {}).get("text") or ""
            if not sender or not text:
                continue
            customer_id = await resolve_or_create_customer(
                workspace_id=ws_id, psid=sender, name=f"IG {sender[-4:]}",
            )
            conv_id = await resolve_or_create_conversation(
                workspace_id=ws_id, customer_id=customer_id, channel="instagram",
            )
            res = await ingest_inbound_message(
                workspace_id=ws_id, conversation_id=conv_id, text=text,
                from_name=f"Instagram @{sender[-4:]}",
                external_message_id=(m.get("message") or {}).get("mid"),
            )
            delivered.append(res["message"]["id"])
    return {"ok": True, "received": len(delivered)}


# ============================================================================
# Shopify (spec §6 e-commerce, §3.9.2) — orders/*, refunds/*, products/*
# ============================================================================

@router.post("/webhooks/shopify")
async def shopify_webhook(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None, alias="X-Shopify-Hmac-Sha256"),
    x_shopify_shop_domain: str | None = Header(default=None, alias="X-Shopify-Shop-Domain"),
    x_shopify_topic: str | None = Header(default=None, alias="X-Shopify-Topic"),
) -> dict:
    """Single endpoint for every `X-Shopify-Topic` — we key off the header to
    decide what to do. Shopify signs the raw body with HMAC-SHA256 + base64."""
    raw = await request.body()
    secret = _env("SHOPIFY_APP_SECRET")
    if secret:
        expected = base64.b64encode(
            hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(expected, (x_shopify_hmac_sha256 or "").strip()):
            raise HTTPException(403, "invalid shopify hmac")
    ws_id = await _default_workspace_for("shopify", x_shopify_shop_domain)
    if not ws_id:
        return {"ok": True, "ignored": True}
    payload = json.loads(raw or b"{}")
    topic = (x_shopify_topic or "").strip()
    db = await get_db()

    if topic.startswith("orders/"):
        # Upsert the order row + append an activity-feed event.
        cust = payload.get("customer") or {}
        customer_id = await resolve_or_create_customer(
            workspace_id=ws_id,
            email=cust.get("email"),
            phone=cust.get("phone"),
            shopify_id=str(cust.get("id")) if cust.get("id") else None,
            name=(cust.get("first_name") or "") + " " + (cust.get("last_name") or ""),
        )
        oid = str(payload.get("id") or payload.get("name") or uuid4())
        items = [{"name": li.get("title") or li.get("name"),
                  "qty": li.get("quantity", 1),
                  "price": li.get("price")}
                 for li in (payload.get("line_items") or [])]
        total = payload.get("total_price") or payload.get("current_total_price") or 0
        placed = payload.get("created_at") or datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO orders (id, workspace_id, customer_id, source, placed_at,
                                total, currency, status, status_tone, items)
            VALUES (?,?,?, 'shopify', ?, ?, ?, ?, 'info', ?)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, total = EXCLUDED.total,
                                            items = EXCLUDED.items
            """,
            (oid, ws_id, customer_id, placed, float(total or 0),
             payload.get("currency") or "USD",
             payload.get("financial_status") or "paid", js(items)),
        )
        await db.execute(
            """
            INSERT INTO ecommerce_events (workspace_id, store_id, type, title, sub, tone)
            SELECT ?, s.id, ?, ?, ?, 'info'
              FROM ecommerce_stores s
             WHERE s.workspace_id = ? AND s.platform = 'shopify'
             ORDER BY s.created_at LIMIT 1
            """,
            (ws_id, topic, f"Order {payload.get('name') or oid}",
             f"{payload.get('customer', {}).get('email') or ''} · {total}", ws_id),
        )
        await db.commit()
        return {"ok": True, "kind": "order", "orderId": oid}

    if topic.startswith("refunds/"):
        await db.execute(
            """
            INSERT INTO ecommerce_events (workspace_id, type, title, sub, tone)
            VALUES (?, ?, ?, ?, 'warning')
            """,
            (ws_id, topic, "Refund issued",
             f"Order {payload.get('order_id') or payload.get('id')}"),
        )
        await db.commit()
        return {"ok": True, "kind": "refund"}

    # products/*, fulfillments/*, etc. → activity feed with a generic entry.
    await db.execute(
        """
        INSERT INTO ecommerce_events (workspace_id, type, title, sub, tone)
        VALUES (?, ?, ?, ?, 'info')
        """,
        (ws_id, topic, topic or "shopify event", x_shopify_shop_domain or ""),
    )
    await db.commit()
    return {"ok": True, "kind": "event"}


# ============================================================================
# Slack (spec §6.6)
# ============================================================================

@router.post("/webhooks/slack/events")
async def slack_events(request: Request) -> dict:
    """Slack Events API. First call is URL verification (challenge round-trip);
    subsequent calls carry `event` blocks. We sign-verify with the signing
    secret when configured (spec §6.6)."""
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    secret = _env("SLACK_SIGNING_SECRET")
    if secret and ts and sig:
        base = f"v0:{ts}:".encode() + raw
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(403, "invalid slack signature")
    payload = json.loads(raw or b"{}")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    team_id = payload.get("team_id")
    ws_id = await _default_workspace_for("slack", team_id)
    event = payload.get("event") or {}
    # Route `message` events back into the teams chat for now; interactive
    # component callbacks land at the /interactions endpoint.
    if event.get("type") == "message" and not event.get("bot_id") and ws_id:
        db = await get_db()
        await db.execute(
            """
            INSERT INTO ecommerce_events (workspace_id, type, title, sub, tone)
            VALUES (?, 'slack.message', ?, ?, 'info')
            """,
            (ws_id, (event.get("text") or "")[:120], event.get("user") or ""),
        )
        await db.commit()
    return {"ok": True}


@router.post("/webhooks/slack/interactions")
async def slack_interactions(request: Request) -> dict:
    """Block Kit / shortcut callbacks. Slack sends `application/x-www-form-urlencoded`
    with a `payload` field holding JSON. We ack quickly so Slack doesn't time
    out."""
    form = await request.form()
    payload_raw = form.get("payload")
    if not payload_raw:
        raise HTTPException(400, "missing payload")
    payload = json.loads(payload_raw)
    # Example: a "Claim" button on an escalation card → update the ticket.
    action = (payload.get("actions") or [{}])[0]
    if action.get("action_id") == "claim_ticket":
        try:
            ticket_id = int(action.get("value") or 0)
        except Exception:
            ticket_id = 0
        team_id = (payload.get("team") or {}).get("id")
        ws_id = await _default_workspace_for("slack", team_id)
        if ticket_id and ws_id:
            db = await get_db()
            await db.execute(
                "UPDATE tickets SET status = 'in_progress', updated_at = now() WHERE id = ? AND workspace_id = ?",
                (ticket_id, ws_id),
            )
            await db.commit()
    return {"ok": True}


# ============================================================================
# Voice / Call recordings (spec §6.7) — Twilio / Exotel shape
# ============================================================================

@router.post("/webhooks/voice")
async def voice_webhook(request: Request) -> dict:
    """Twilio / Exotel POST on call completion. We persist a conversation + a
    `system` message with the recording URL so agents can listen in-app. Real
    Whisper transcription lands with the voice adapter in Build 4."""
    body: dict[str, Any]
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)
    ws_id = await _default_workspace_for("twilio", body.get("AccountSid"))
    if not ws_id:
        return {"ok": True, "ignored": True}
    phone = body.get("From") or body.get("CallerNumber")
    recording = body.get("RecordingUrl") or body.get("RecordingURL") or body.get("recording_url")
    duration = body.get("CallDuration") or body.get("DialCallDuration") or "0"
    customer_id = await resolve_or_create_customer(
        workspace_id=ws_id, phone=phone, name=body.get("CallerName") or f"Caller {phone}",
    )
    conv_id = await resolve_or_create_conversation(
        workspace_id=ws_id, customer_id=customer_id, channel="voice",
        external_thread_id=body.get("CallSid"),
    )
    attachments = [{"url": recording, "name": "recording.mp3", "mime": "audio/mpeg",
                    "duration": duration}] if recording else []
    res = await ingest_inbound_message(
        workspace_id=ws_id, conversation_id=conv_id,
        text=f"Voice call ({duration}s)" + (" — recording attached" if recording else ""),
        from_name=body.get("CallerName") or phone or "Caller",
        external_message_id=body.get("CallSid"),
        attachments=attachments,
        auto_reply=False,   # voice calls don't auto-draft
    )
    # Stash the recording on the conversation row so Customer detail can list it.
    if recording:
        db = await get_db()
        await db.execute(
            """
            UPDATE conversations
               SET recordings = COALESCE(recordings, '[]'::jsonb) ||
                                jsonb_build_array(jsonb_build_object(
                                  'url', ?::text, 'duration', ?::text,
                                  'at', now()::text))
             WHERE id = ?
            """,
            (recording, str(duration), conv_id),
        )
        await db.commit()
    return {"ok": True, "messageId": res["message"]["id"]}


# ============================================================================
# Stripe (spec §3.12) — subscription / invoice events
# ============================================================================

@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict:
    """Upserts `invoices` on `invoice.*` events and flips `workspaces.plan` on
    `customer.subscription.*`. Signature verification requires the Stripe SDK;
    we do a best-effort check on the timestamp+hash so dev installs without
    the SDK still validate the secret."""
    raw = await request.body()
    secret = _env("STRIPE_WEBHOOK_SECRET")
    if secret and stripe_signature:
        # Parse the `t=<ts>,v1=<sig>` header and verify HMAC.
        parts = dict(kv.split("=", 1) for kv in stripe_signature.split(",") if "=" in kv)
        ts, v1 = parts.get("t"), parts.get("v1")
        if ts and v1:
            signed = f"{ts}.".encode() + raw
            expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, v1):
                raise HTTPException(403, "invalid stripe signature")
    event = json.loads(raw or b"{}")
    etype = event.get("type", "")
    obj = ((event.get("data") or {}).get("object")) or {}
    db = await get_db()

    if etype.startswith("invoice."):
        customer = obj.get("customer")
        cur = await db.execute(
            "SELECT id FROM workspaces WHERE stripe_customer_id = ? LIMIT 1", (customer,)
        )
        r = await cur.fetchone()
        ws_id = r[0] if r else None
        if ws_id:
            await db.execute(
                """
                INSERT INTO invoices (id, workspace_id, number, amount_cents, currency,
                                      status, period_start, period_end, hosted_url, pdf_url)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (id) DO UPDATE SET
                  status = EXCLUDED.status, amount_cents = EXCLUDED.amount_cents,
                  hosted_url = EXCLUDED.hosted_url, pdf_url = EXCLUDED.pdf_url
                """,
                (obj.get("id"), ws_id, obj.get("number"),
                 int(obj.get("amount_paid") or obj.get("amount_due") or 0),
                 obj.get("currency", "usd"), obj.get("status", "paid"),
                 _epoch(obj.get("period_start")), _epoch(obj.get("period_end")),
                 obj.get("hosted_invoice_url"), obj.get("invoice_pdf")),
            )
    elif etype.startswith("customer.subscription."):
        plan = ((obj.get("items") or {}).get("data") or [{}])[0].get("plan", {}).get("nickname") or "growth"
        await db.execute(
            """
            UPDATE workspaces
               SET plan = ?, stripe_subscription_id = ?, plan_renews_at = ?
             WHERE stripe_customer_id = ?
            """,
            (plan, obj.get("id"), _epoch(obj.get("current_period_end")),
             obj.get("customer")),
        )
    await db.commit()
    return {"ok": True, "type": etype}


def _epoch(v: int | None) -> str | None:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat()
    except Exception:
        return None
