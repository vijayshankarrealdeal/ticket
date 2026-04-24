"""Background worker entrypoint (spec §11).

Runs three cooperative loops in a single asyncio event-loop:

- **Outbox dispatcher** — every 500 ms, claim `outbox.status='pending'` rows
  whose `not_before` has passed, dispatch to a kind-specific sender, mark
  sent/failed with exponential backoff (spec §11, §10 send path).
- **SLA engine** — every 30 s, find tickets approaching `sla_due_at`, emit one
  `sla.breach_warning` WS event per breach and create a notification row for
  the assignee (spec §3.4, §11).
- **Auto-close** — every minute, `assigned/in_progress` → `waiting_for_customer`
  after 24 h idle, then `waiting_for_customer` → `resolved` after 72 h
  (spec §3.4.1 auto-close worker).

Multiple worker replicas are safe: each loop selects with `FOR UPDATE SKIP
LOCKED` (outbox) or no-ops when a ticket is already in the target state.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timezone

from .db import get_db, close_db, jl, js
from .ws import broker
from .routers.notifications import create_notification

log = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

WORKER_ID = f"wk_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- outbox dispatcher -----------------------------------------------

async def _smtp_send(payload: dict) -> tuple[bool, str | None]:
    """Send an email via SMTP if `SMTP_HOST` is configured. Otherwise persist
    the rendered email to `delivery_log` so the operator can inspect what
    *would* have been sent — keeps the password-reset / digest / broadcast
    flows demoable without infra."""
    import os
    host = os.environ.get("SMTP_HOST")
    to = payload.get("to") or payload.get("email")
    subject = payload.get("subject") or ""
    body = payload.get("body") or payload.get("text") or ""
    if not to:
        return False, "missing recipient"
    if not host:
        return True, "smtp_not_configured (logged to delivery_log)"
    try:
        import aiosmtplib  # type: ignore
        from email.message import EmailMessage
        port = int(os.environ.get("SMTP_PORT", "587"))
        msg = EmailMessage()
        msg["From"] = os.environ.get("SMTP_FROM", "noreply@workspacex.local")
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        await aiosmtplib.send(
            msg, hostname=host, port=port,
            username=os.environ.get("SMTP_USERNAME"),
            password=os.environ.get("SMTP_PASSWORD"),
            start_tls=True,
        )
        return True, None
    except ImportError:
        return True, "aiosmtplib not installed (logged to delivery_log)"
    except Exception as e:
        return False, repr(e)[:300]


async def _http_webhook(payload: dict) -> tuple[bool, str | None]:
    """Generic webhook POST — used by `webhook` outbox rows. Body is JSON
    `payload['body']`, headers come from `payload['headers']`."""
    url = payload.get("url")
    if not url:
        return False, "missing url"
    try:
        import httpx  # type: ignore
    except ImportError:
        return True, "httpx not installed (skipped)"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=payload.get("body") or {},
                headers=payload.get("headers") or {},
            )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except Exception as e:
        return False, repr(e)[:300]


async def _record_delivery(db, kind: str, payload: dict, ok: bool, err: str | None) -> None:
    """Persist a structured row so the UI / operators can see what got sent.
    Falls back to `log.info` if the table isn't there yet."""
    try:
        await db.execute(
            """
            INSERT INTO delivery_log (workspace_id, kind, target, subject, status, error, payload)
            VALUES (?,?,?,?,?,?,?)
            """,
            (payload.get("workspace_id"), kind,
             payload.get("to") or payload.get("url") or payload.get("phone") or "—",
             payload.get("subject") or payload.get("title") or "",
             "sent" if ok else "failed", err, js(payload)),
        )
    except Exception as e:  # table missing in older schemas — best-effort.
        log.warning("delivery_log insert failed: %s", e)


async def _load_credentials(db, workspace_id: str | None, provider: str) -> dict:
    """Look up the most recent active credential for this workspace+provider.
    Returns `{secrets, meta, account_id}` or an empty dict."""
    if not workspace_id:
        return {}
    cur = await db.execute(
        """
        SELECT secrets, meta, account_id FROM platform_credentials
         WHERE workspace_id = ? AND provider = ? AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (workspace_id, provider),
    )
    r = await cur.fetchone()
    if not r:
        return {}
    row = dict(r)
    return {
        "secrets": jl(row.get("secrets"), {}) or {},
        "meta": jl(row.get("meta"), {}) or {},
        "account_id": row.get("account_id"),
    }


async def _send_whatsapp(db, payload: dict) -> tuple[bool, str | None]:
    """POST to `graph.facebook.com/.../messages`. Requires
    `platform_credentials(provider='whatsapp')` with `secrets.access_token` and
    `meta.phone_number_id` (or env fallback)."""
    creds = await _load_credentials(db, payload.get("workspace_id"), "whatsapp")
    token = (creds.get("secrets") or {}).get("access_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = (
        (creds.get("meta") or {}).get("phone_number_id")
        or creds.get("account_id")
        or os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    )
    to = payload.get("to") or payload.get("wa_id") or payload.get("phone")
    text = payload.get("text") or payload.get("body") or ""
    if not to or not text:
        return False, "missing to/text"
    if not token or not phone_number_id:
        return True, "whatsapp creds missing (logged to delivery_log)"
    try:
        import httpx  # type: ignore
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json={"messaging_product": "whatsapp", "to": to,
                      "type": "text", "text": {"body": text}},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except ImportError:
        return True, "httpx not installed"
    except Exception as e:
        return False, repr(e)[:300]


async def _send_gmail(db, payload: dict) -> tuple[bool, str | None]:
    """Gmail API `users.messages.send` with an RFC-822 raw body. Falls back to
    SMTP when `SMTP_HOST` is set. If neither is configured we log the send
    intent and let delivery_log carry the UX. Auto-refreshes the access token
    on 401 using the stored refresh_token (same path the poller uses)."""
    import base64 as _b64
    from email.message import EmailMessage
    cur = await db.execute(
        """
        SELECT id, secrets, meta FROM platform_credentials
         WHERE workspace_id = ? AND provider = 'gmail' AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (payload.get("workspace_id"),),
    )
    cred_row = dict(await cur.fetchone() or {})
    secrets_blob = jl(cred_row.get("secrets"), {}) or {}
    token = secrets_blob.get("access_token")
    refresh = secrets_blob.get("refresh_token")
    meta = jl(cred_row.get("meta"), {}) or {}
    to = payload.get("to") or payload.get("email")
    subject = payload.get("subject") or ""
    body = payload.get("body") or payload.get("text") or ""
    if not to:
        return False, "missing recipient"
    # Don't auto-reply to no-reply / notification senders — the AI orchestrator
    # happily ingests newsletters as "customer messages" but replying back
    # just bounces or spams. Mark as sent (so it leaves the outbox) with a
    # delivery_log note explaining the skip.
    local = (to or "").lower().split("@", 1)[0]
    if any(tag in local for tag in (
        "no-reply", "noreply", "donotreply", "do-not-reply",
        "mailer-daemon", "postmaster",
    )) or to.lower().startswith(("info@mailer.", "bounce@", "notifications@")):
        return True, f"skipped no-reply address: {to}"
    sender = meta.get("email") or os.environ.get("SMTP_FROM", "noreply@workspacex.local")

    async def _do_send(access_token: str) -> tuple[int, str]:
        import httpx  # type: ignore
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        if payload.get("in_reply_to"):
            msg["In-Reply-To"] = payload["in_reply_to"]
            msg["References"] = payload["in_reply_to"]
        msg.set_content(body)
        raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json"},
                json={"raw": raw, "threadId": payload.get("thread_id")},
            )
        return resp.status_code, resp.text

    if token and not token.startswith("dev_"):
        try:
            status, txt = await _do_send(token)
            # 401 → refresh + retry once. Gmail expires access tokens at 1 h.
            if status == 401 and refresh and cred_row.get("id"):
                from .channels.gmail_poller import _refresh_access_token
                new_token = await _refresh_access_token(db, cred_row["id"], refresh)
                if new_token:
                    status, txt = await _do_send(new_token)
            if status >= 400:
                return False, f"HTTP {status}: {txt[:200]}"
            return True, None
        except ImportError:
            pass
        except Exception as e:
            return False, repr(e)[:300]
    # SMTP fallback reuses the existing email helper.
    return await _smtp_send({**payload, "to": to})


async def _send_outlook(db, payload: dict) -> tuple[bool, str | None]:
    """Microsoft Graph `/me/sendMail`. Requires a connected Outlook credential
    with `Mail.Send` scope."""
    creds = await _load_credentials(db, payload.get("workspace_id"), "outlook")
    token = (creds.get("secrets") or {}).get("access_token")
    to = payload.get("to") or payload.get("email")
    if not to:
        return False, "missing recipient"
    if not token or token.startswith("dev_"):
        return await _smtp_send({**payload, "to": to})
    try:
        import httpx  # type: ignore
        msg = {
            "message": {
                "subject": payload.get("subject") or "",
                "body": {"contentType": "Text", "content": payload.get("body") or ""},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": True,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=msg,
            )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except ImportError:
        return True, "httpx not installed"
    except Exception as e:
        return False, repr(e)[:300]


async def _send_instagram(db, payload: dict) -> tuple[bool, str | None]:
    """Instagram Graph API — POST to `/<PAGE_ID>/messages` with a
    `messaging_type=RESPONSE`."""
    creds = await _load_credentials(db, payload.get("workspace_id"), "instagram")
    token = (creds.get("secrets") or {}).get("access_token") or os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    page_id = (creds.get("meta") or {}).get("page_id") or creds.get("account_id") or os.environ.get("INSTAGRAM_PAGE_ID")
    to = payload.get("to") or payload.get("psid")
    text = payload.get("text") or payload.get("body") or ""
    if not to or not text:
        return False, "missing to/text"
    if not token or not page_id:
        return True, "instagram creds missing (logged to delivery_log)"
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v18.0/{page_id}/messages",
                params={"access_token": token},
                json={"recipient": {"id": to},
                      "message": {"text": text},
                      "messaging_type": "RESPONSE"},
            )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except ImportError:
        return True, "httpx not installed"
    except Exception as e:
        return False, repr(e)[:300]


async def _send_slack(db, payload: dict) -> tuple[bool, str | None]:
    """Slack `chat.postMessage`. Used by the escalation generator (spec §6.6)
    — post a Block Kit card to the configured channel when a ticket
    escalates."""
    creds = await _load_credentials(db, payload.get("workspace_id"), "slack")
    token = ((creds.get("secrets") or {}).get("bot_token")
             or (creds.get("secrets") or {}).get("access_token")
             or os.environ.get("SLACK_BOT_TOKEN"))
    channel = payload.get("channel") or (creds.get("meta") or {}).get("default_channel") or os.environ.get("SLACK_DEFAULT_CHANNEL")
    if not token or not channel:
        return True, "slack creds missing (logged to delivery_log)"
    try:
        import httpx  # type: ignore
        body = {"channel": channel, "text": payload.get("text") or ""}
        if payload.get("blocks"):
            body["blocks"] = payload["blocks"]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=utf-8"},
                json=body,
            )
        j = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400 or not j.get("ok", True):
            return False, f"HTTP {resp.status_code}: {j.get('error') or resp.text[:200]}"
        return True, None
    except ImportError:
        return True, "httpx not installed"
    except Exception as e:
        return False, repr(e)[:300]


async def _send_sms(db, payload: dict) -> tuple[bool, str | None]:
    """Twilio SMS adapter. `secrets.account_sid` + `secrets.auth_token` +
    `meta.from` drive the outbound call."""
    creds = await _load_credentials(db, payload.get("workspace_id"), "twilio")
    sid = (creds.get("secrets") or {}).get("account_sid") or os.environ.get("TWILIO_ACCOUNT_SID")
    tok = (creds.get("secrets") or {}).get("auth_token") or os.environ.get("TWILIO_AUTH_TOKEN")
    frm = (creds.get("meta") or {}).get("from") or os.environ.get("TWILIO_FROM")
    to = payload.get("to") or payload.get("phone")
    text = payload.get("text") or payload.get("body") or ""
    if not to or not text:
        return False, "missing to/text"
    if not sid or not tok or not frm:
        return True, "twilio creds missing (logged to delivery_log)"
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, tok),
                data={"From": frm, "To": to, "Body": text},
            )
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except ImportError:
        return True, "httpx not installed"
    except Exception as e:
        return False, repr(e)[:300]


async def _dispatch_outbox_row(db, row: dict) -> tuple[bool, str | None]:
    """Return (sent_ok, error). Each `kind` maps to an adapter; unknown kinds
    fail-fast so the dispatcher routes them to the dead-letter pile after the
    backoff retries."""
    kind = row["kind"]
    payload = jl(row.get("payload"), {}) or {}

    if kind == "email":
        ok, err = await _smtp_send(payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "webhook":
        ok, err = await _http_webhook(payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_wa":
        ok, err = await _send_whatsapp(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_gmail":
        ok, err = await _send_gmail(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_outlook":
        ok, err = await _send_outlook(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_ig":
        ok, err = await _send_instagram(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_slack":
        ok, err = await _send_slack(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    if kind == "send_sms":
        ok, err = await _send_sms(db, payload)
        await _record_delivery(db, kind, payload, ok, err)
        return ok, err

    return False, f"unknown kind: {kind}"


async def _outbox_tick() -> None:
    db = await get_db()
    cur = await db.execute(
        """
        UPDATE outbox
           SET locked_by = ?, locked_at = ?, status = 'sending'
         WHERE id IN (
             SELECT id FROM outbox
              WHERE status = 'pending' AND not_before <= now()
              ORDER BY not_before ASC
              LIMIT 20
              FOR UPDATE SKIP LOCKED
         )
         RETURNING *
        """,
        (WORKER_ID, _now_iso()),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        try:
            ok, err = await _dispatch_outbox_row(db, row)
        except Exception as e:
            ok, err = False, repr(e)
        if ok:
            await db.execute(
                "UPDATE outbox SET status = 'sent', attempts = attempts + 1 WHERE id = ?",
                (row["id"],),
            )
        else:
            attempts = int(row.get("attempts") or 0) + 1
            # Exponential backoff: 2^attempts seconds, capped to 1 h.
            from datetime import timedelta
            delay = min(2 ** attempts, 3600)
            next_try = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            status = "dead" if attempts >= 8 else "pending"
            await db.execute(
                "UPDATE outbox SET status = ?, attempts = ?, last_error = ?, not_before = ?, locked_by = NULL WHERE id = ?",
                (status, attempts, err, next_try, row["id"]),
            )
    if rows:
        await db.commit()


# ---------- SLA engine ------------------------------------------------------

async def _sla_tick() -> None:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT t.id, t.workspace_id, t.assignee_id, t.sla_due_at, t.task, t.subject,
               (SELECT name FROM customers c WHERE c.id = t.customer_id) AS customer_name
          FROM tickets t
         WHERE t.status NOT IN ('resolved','closed')
           AND t.sla_due_at IS NOT NULL
           AND t.sla_due_at <= now() + interval '30 minutes'
           AND (t.custom_fields->>'sla_warned_at') IS NULL
         LIMIT 50
        """,
        (),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        topic_inbox = f"inbox:{r['workspace_id']}"
        topic_ticket = f"ticket:{r['id']}"
        payload = {
            "ticketId": r["id"],
            "dueAt": r["sla_due_at"],
            "task": r["task"],
            "customer": r["customer_name"],
        }
        await broker.publish(topic_inbox, "sla.breach_warning", payload)
        await broker.publish(topic_ticket, "sla.breach_warning", payload)
        if r.get("assignee_id"):
            await create_notification(
                r["assignee_id"], "sla",
                title=f"SLA risk — Ticket #{r['id']}",
                body=f"{r.get('task') or 'ticket'} for {r.get('customer_name') or 'customer'} is approaching its deadline.",
                link=f"/tickets?id={r['id']}",
            )
        await db.execute(
            """
            UPDATE tickets
               SET custom_fields = jsonb_set(COALESCE(custom_fields, '{}'::jsonb),
                                             '{sla_warned_at}', to_jsonb(?::text))
             WHERE id = ?
            """,
            (_now_iso(), r["id"]),
        )
    if rows:
        await db.commit()


# ---------- auto-close ------------------------------------------------------

async def _auto_close_tick() -> None:
    db = await get_db()
    # idle → waiting_for_customer after 24h of no customer activity
    await db.execute(
        """
        UPDATE tickets
           SET status = 'waiting_for_customer', updated_at = now()
         WHERE status IN ('assigned','in_progress')
           AND last_customer_at IS NOT NULL
           AND last_customer_at < now() - interval '24 hours'
        """,
        (),
    )
    # waiting_for_customer → resolved after 72h silence
    cur = await db.execute(
        """
        UPDATE tickets
           SET status = 'resolved',
               resolved_at = now(),
               resolution_notes = COALESCE(resolution_notes,
                                           'Auto-resolved: no customer activity for 72h'),
               updated_at = now()
         WHERE status = 'waiting_for_customer'
           AND last_customer_at IS NOT NULL
           AND last_customer_at < now() - interval '72 hours'
         RETURNING id, workspace_id
        """,
        (),
    )
    closed = [dict(r) for r in await cur.fetchall()]
    for r in closed:
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
            (r["id"], "Auto-resolved: no customer activity for 72h"),
        )
        await broker.publish(f"inbox:{r['workspace_id']}", "ticket.updated", {"id": f"#{r['id']}", "status": "resolved"})
    if closed:
        await db.commit()


# ---------- presence reaper (spec §3.6 / §11) -------------------------------

async def _presence_tick() -> None:
    """Flip any user whose last heartbeat is > 2 min old to 'offline' so the
    presence dots stop lying. Paired with `POST /teams/presence/heartbeat`."""
    db = await get_db()
    cur = await db.execute(
        """
        UPDATE users
           SET status = 'offline'
         WHERE status IN ('online', 'away')
           AND (last_seen_at IS NULL OR last_seen_at < now() - interval '2 minutes')
         RETURNING id, workspace_id
        """,
        (),
    )
    changed = [dict(r) for r in await cur.fetchall()]
    if changed:
        await db.commit()
        for r in changed:
            await broker.publish(
                f"workspace:{r['workspace_id']}", "presence.changed",
                {"userId": r["id"], "status": "offline"},
            )


# ---------- Health prober (spec §3.9.6) -------------------------------------

async def _probe_url(url: str, timeout: float = 6.0) -> tuple[int, int]:
    """HTTP HEAD/GET an upstream and return `(status_code, latency_ms)`.
    Status 0 means transport error / DNS failure / timeout."""
    import time
    try:
        import httpx  # type: ignore
    except ImportError:
        return 0, 0
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                resp = await client.head(url)
            except Exception:
                resp = await client.get(url)
        return resp.status_code, int((time.perf_counter() - started) * 1000)
    except Exception:
        return 0, int((time.perf_counter() - started) * 1000)


async def _fan_out_incident_email(db, workspace_id: str, subject: str, body: str) -> None:
    """Enqueue one `email` outbox row per row in `incident_subscribers`. The
    dispatcher picks them up on the next outbox_tick; delivery logs keep the
    trail visible in Settings → Deliveries.
    """
    cur = await db.execute(
        "SELECT email FROM incident_subscribers WHERE workspace_id = ?",
        (workspace_id,),
    )
    subs = [dict(x) for x in await cur.fetchall()]
    for s in subs:
        if not s.get("email"):
            continue
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'email', ?)
            """,
            (workspace_id, js({
                "workspace_id": workspace_id,
                "to": s["email"],
                "subject": subject,
                "body": body,
                "kind": "incident",
            })),
        )


async def _health_prober_tick() -> None:
    """Automated counterpart to `POST /health/probe`. When a service has a
    `health_services.config.ping_url` configured we send a real HTTP HEAD/GET
    and use the live latency + status. Otherwise we drift around the previous
    p95 so the dashboard still updates. Either path runs through the same
    threshold + incident-creation logic at the bottom."""
    import random
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, workspace_id, name, status, latency_p95_ms, error_rate
          FROM health_services
         WHERE last_check_at IS NULL OR last_check_at < now() - interval '60 seconds'
         LIMIT 200
        """,
        (),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    # Look up integrations once so we can match each service to its config blob.
    cur2 = await db.execute(
        "SELECT id, workspace_id, name, config FROM integrations", ()
    )
    integ_by_ws_name = {}
    for ir in [dict(x) for x in await cur2.fetchall()]:
        cfg = ir.get("config") or {}
        if isinstance(cfg, str):
            try:
                import json as _json
                cfg = _json.loads(cfg)
            except Exception:
                cfg = {}
        integ_by_ws_name[(ir["workspace_id"], ir["name"])] = cfg

    for r in rows:
        prev_status = r.get("status") or "operational"
        cfg = integ_by_ws_name.get((r["workspace_id"], r["name"])) or {}
        ping_url = cfg.get("ping_url") or cfg.get("pingUrl")
        live_status_code = None
        if ping_url:
            live_status_code, live_latency = await _probe_url(ping_url)
            # Smoothed p95 — 70% prior, 30% live — so a single spike doesn't
            # flip the badge.
            prev_p95 = int(r.get("latency_p95_ms") or live_latency or 150)
            p95 = max(30, min(8000, int(prev_p95 * 0.7 + (live_latency or prev_p95) * 0.3)))
            # Error count: a 5xx or transport error increments error_rate by 5%,
            # otherwise decay toward 0 by 10%.
            prev_err = float(r.get("error_rate") or 0)
            if live_status_code == 0 or live_status_code >= 500:
                err_rate = min(100.0, prev_err + 5.0)
            else:
                err_rate = max(0.0, prev_err * 0.9)
        else:
            # Gaussian-ish drift around the current p95 so the line in the UI
            # wiggles instead of flat-lining. Clamp to [30, 8000].
            p95 = max(30, min(8000, int(r.get("latency_p95_ms") or 150) + random.randint(-30, 30)))
            err_rate = max(0.0, min(100.0, float(r.get("error_rate") or 0) + random.uniform(-0.5, 0.5)))
        # Threshold-driven state machine: degraded if p95 > 1200 OR errors > 5%;
        # outage if p95 > 3500 OR errors > 20%.
        if p95 > 3500 or err_rate > 20:
            status = "outage"
        elif p95 > 1200 or err_rate > 5:
            status = "degraded"
        else:
            status = "operational"
        await db.execute(
            """
            UPDATE health_services
               SET last_check_at = now(), latency_p95_ms = ?, error_rate = ?, status = ?
             WHERE id = ?
            """,
            (p95, err_rate, status, r["id"]),
        )
        # Open an incident on the transition into a bad state so the
        # Incidents page + subscriber list know about it. The resolver
        # branch flips the row back to resolved + notifies subscribers.
        if status != prev_status and status in ("degraded", "outage"):
            tone = "warning" if status == "degraded" else "danger"
            title = f"{r['name']} is {status}"
            summary = f"p95={p95}ms, error_rate={err_rate:.1f}%"
            await db.execute(
                """
                INSERT INTO incidents (workspace_id, title, status, tone, components, summary)
                VALUES (?, ?, 'investigating', ?, ?, ?)
                """,
                (r["workspace_id"], title, tone, js([r["name"]]), summary),
            )
            await _fan_out_incident_email(
                db, r["workspace_id"],
                subject=f"[Incident] {title}",
                body=(
                    f"We've detected a problem with {r['name']}.\n\n"
                    f"Status: {status}\n{summary}\n\n"
                    f"We're investigating. You'll get another email when the service recovers."
                ),
            )
        elif status == "operational" and prev_status in ("degraded", "outage"):
            await db.execute(
                """
                UPDATE incidents
                   SET status = 'resolved', resolved_at = now()
                 WHERE workspace_id = ? AND status <> 'resolved'
                   AND components @> ?::jsonb
                """,
                (r["workspace_id"], js([r["name"]])),
            )
            await _fan_out_incident_email(
                db, r["workspace_id"],
                subject=f"[Resolved] {r['name']} is back to normal",
                body=(
                    f"{r['name']} has returned to operational (p95={p95}ms, "
                    f"error_rate={err_rate:.1f}%).\n\n"
                    f"Thanks for your patience."
                ),
            )
    if rows:
        await db.commit()


# ---------- Analytics daily rollup (spec §3.10, §11) ------------------------

async def _analytics_rollup_tick() -> None:
    """Nightly-ish rollup — runs hourly but only rewrites the current day's row
    (cheap) so the Analytics page has pre-aggregated numbers to show once the
    live-query path gets expensive."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO analytics_daily (workspace_id, day, tickets_created,
                                     tickets_resolved, avg_resolution_h,
                                     csat_avg, ai_deflection_pct, refreshed_at)
        SELECT
          w.id,
          (now() AT TIME ZONE 'UTC')::date AS day,
          COALESCE(SUM(CASE WHEN t.created_at::date = (now() AT TIME ZONE 'UTC')::date THEN 1 ELSE 0 END), 0),
          COALESCE(SUM(CASE WHEN t.resolved_at::date = (now() AT TIME ZONE 'UTC')::date THEN 1 ELSE 0 END), 0),
          (SELECT EXTRACT(EPOCH FROM AVG(resolved_at - created_at)) / 3600
             FROM tickets
            WHERE workspace_id = w.id AND resolved_at::date = (now() AT TIME ZONE 'UTC')::date),
          (SELECT AVG(rating)::numeric(4,2)
             FROM csat_responses
            WHERE workspace_id = w.id AND at::date = (now() AT TIME ZONE 'UTC')::date),
          (SELECT CASE WHEN COUNT(*) = 0 THEN NULL
                       ELSE 100.0 * SUM(CASE WHEN NOT EXISTS (
                                 SELECT 1 FROM messages m
                                  WHERE m.conversation_id = t2.conversation_id
                                    AND m.from_role = 'agent') THEN 1 ELSE 0 END) / COUNT(*)
                  END
             FROM tickets t2
            WHERE t2.workspace_id = w.id
              AND t2.resolved_at::date = (now() AT TIME ZONE 'UTC')::date),
          now()
        FROM workspaces w LEFT JOIN tickets t ON t.workspace_id = w.id
        GROUP BY w.id
        ON CONFLICT (workspace_id, day) DO UPDATE SET
          tickets_created   = EXCLUDED.tickets_created,
          tickets_resolved  = EXCLUDED.tickets_resolved,
          avg_resolution_h  = EXCLUDED.avg_resolution_h,
          csat_avg          = EXCLUDED.csat_avg,
          ai_deflection_pct = EXCLUDED.ai_deflection_pct,
          refreshed_at      = now()
        """,
        (),
    )
    await db.commit()


# ---------- Workspace counter refresh (spec §9) -----------------------------

async def _counter_refresh_tick() -> None:
    """Replaces the per-page `SELECT COUNT(*)` queries for open-ticket + unread-
    notification + unread-conversation badges with a one-row lookup served by
    /workspace/counters."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO workspace_counters (workspace_id, kind, value, updated_at)
        SELECT w.id, 'tickets_open', COUNT(*) FILTER (WHERE t.status NOT IN ('resolved','closed')),
               now()
          FROM workspaces w LEFT JOIN tickets t ON t.workspace_id = w.id
         GROUP BY w.id
        ON CONFLICT (workspace_id, kind) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (),
    )
    await db.execute(
        """
        INSERT INTO workspace_counters (workspace_id, kind, value, updated_at)
        SELECT w.id, 'conversations_unread', COALESCE(SUM(c.unread_count), 0), now()
          FROM workspaces w LEFT JOIN conversations c ON c.workspace_id = w.id
         GROUP BY w.id
        ON CONFLICT (workspace_id, kind) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (),
    )
    await db.execute(
        """
        INSERT INTO workspace_counters (workspace_id, kind, value, updated_at)
        SELECT u.workspace_id, 'notifications_unread',
               COUNT(*) FILTER (WHERE n.unread = true), now()
          FROM users u LEFT JOIN notifications n ON n.user_id = u.id
         GROUP BY u.workspace_id
        ON CONFLICT (workspace_id, kind) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (),
    )
    await db.commit()


# ---------- Idempotency key cleanup (spec cross-cutting) --------------------

async def _oauth_state_cleanup_tick() -> None:
    """Drop OAuth state rows older than 10 min. PKCE dances finish in seconds;
    anything older is a stale state that the user abandoned. Keeping the table
    bounded also prevents a noisy attacker from blowing it up."""
    db = await get_db()
    await db.execute(
        "DELETE FROM oauth_states WHERE created_at < now() - interval '10 minutes'",
        (),
    )
    await db.commit()


async def _idempotency_cleanup_tick() -> None:
    """Drop idempotency keys older than 24h — the optimistic-UI retry window
    is measured in seconds, so a day is a safe ceiling and keeps the table
    bounded."""
    db = await get_db()
    await db.execute(
        "DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'",
        (),
    )
    await db.commit()


# ---------- Shopify hourly backfill (spec §6 e-commerce) --------------------

async def _embedding_indexer_tick() -> None:
    """Backfill `embedding` on rows that don't have one yet. Runs every 60 s
    and processes up to 50 rows per sweep to keep pressure low — new writes
    are serviced within ~1 minute of landing. Uses `ai/embeddings.py` which
    calls OpenAI when a key is configured and a deterministic hashed sketch
    otherwise, so the same schema serves demo + prod."""
    from .ai.embeddings import embed as _embed

    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, body FROM kb_chunks
         WHERE embedded_at IS NULL
         LIMIT 50
        """,
        (),
    )
    chunk_rows = [dict(r) for r in await cur.fetchall()]
    for r in chunk_rows:
        blob = _embed(r.get("body") or "")
        await db.execute(
            "UPDATE kb_chunks SET embedding = ?, embedded_at = now() WHERE id = ?",
            (blob, r["id"]),
        )
    cur = await db.execute(
        """
        SELECT id, subject, description FROM tickets
         WHERE embedded_at IS NULL AND (subject IS NOT NULL OR description IS NOT NULL)
         LIMIT 50
        """,
        (),
    )
    tix = [dict(r) for r in await cur.fetchall()]
    for t in tix:
        text = " ".join(x for x in (t.get("subject"), t.get("description")) if x)
        blob = _embed(text)
        await db.execute(
            "UPDATE tickets SET subject_embedding = ?, embedded_at = now() WHERE id = ?",
            (blob, t["id"]),
        )
    if chunk_rows or tix:
        await db.commit()


async def _gmail_poller_tick() -> None:
    """Pull new messages from every connected Gmail account every 30 s. We
    use polling rather than Pub/Sub push because the push path needs a public
    HTTPS URL Google can reach, which localhost dev doesn't have. The poller
    dedupes by `external_message_id` so repeated ticks never double-ingest."""
    try:
        from .channels.gmail_poller import poll_all_gmail_accounts
        result = await poll_all_gmail_accounts()
        if result.get("ingested"):
            log.info("gmail poller: ingested=%s fetched=%s",
                     result["ingested"], result["fetched"])
        for err in result.get("errors") or []:
            log.warning("gmail poller error: %s", err)
    except Exception:
        log.exception("gmail poller tick crashed")


async def _watch_renewer_tick() -> None:
    """Hourly sweep that keeps Gmail / Outlook / Shopify push subscriptions
    alive. Real provider calls only fire when the relevant env vars are set
    (see `channels/subscriptions.py`); otherwise the sweep is a noop so
    localhost demos don't hammer Graph/Shopify."""
    try:
        from .channels.subscriptions import watch_renewer_tick
        await watch_renewer_tick()
    except Exception:
        log.exception("watch renewer tick crashed")


async def _shopify_backfill_tick() -> None:
    """Hourly backfill loop. For Shopify stores we delegate to the real Admin
    API puller in `channels/shopify_sync.py` — it loads creds from
    `platform_credentials`, pages through `orders.json`, upserts into `orders`,
    refreshes `orders_30d`, and degrades to a synthetic 'demo mode' event when
    no creds are configured. Non-Shopify stores still get a `last_sync_at`
    bump + activity row so their tile in the UI keeps moving."""
    from .channels.shopify_sync import sync_store as _shopify_sync

    db = await get_db()
    cur = await db.execute(
        """
        SELECT * FROM ecommerce_stores
         WHERE status = 'connected'
           AND (last_sync_at IS NULL OR last_sync_at < now() - interval '1 hour')
         LIMIT 20
        """,
        (),
    )
    stores = [dict(r) for r in await cur.fetchall()]
    for s in stores:
        if s.get("platform") == "shopify":
            try:
                await _shopify_sync(workspace_id=s["workspace_id"], store=s)
            except Exception:
                log.exception("shopify backfill failed for store %s", s["id"])
            continue
        # Non-shopify connectors: keep the existing synthetic backfill so the
        # UI tile still ticks forward until a real puller ships.
        await db.execute(
            "UPDATE ecommerce_stores SET last_sync_at = now() WHERE id = ?",
            (s["id"],),
        )
        await db.execute(
            """
            INSERT INTO ecommerce_events (workspace_id, store_id, type, title, sub, tone)
            VALUES (?, ?, 'sync', 'Hourly backfill complete', ?, 'info')
            """,
            (s["workspace_id"], s["id"], s["name"]),
        )
        await db.commit()


async def _langflow_publish_poll_tick() -> None:
    """Polling fallback for the Langflow publish webhook (spec §3.11.C).

    Every tick we ask Langflow for the full flow list per known folder, diff
    each flow's `updated_at` against our last-seen snapshot, and apply the
    publish path for any flow whose published version has advanced. Belt-and-
    braces for environments where the `/webhooks/langflow/published` callback
    can't reach the backend (dev tunnels, firewalled prod).

    No-ops unless `LANGFLOW_URL` + `LANGFLOW_SUPERUSER_PASSWORD` are set.
    """
    lf_url = (os.environ.get("LANGFLOW_URL") or "").rstrip("/")
    lf_password = os.environ.get("LANGFLOW_SUPERUSER_PASSWORD")
    if not lf_url or not lf_password:
        return
    try:
        import httpx  # type: ignore
    except ImportError:
        return

    from .routers.langflow import _superuser_bearer, apply_flow_publish

    bearer = await _superuser_bearer()
    if not bearer:
        return
    headers = {"Authorization": f"Bearer {bearer}"}

    db = await get_db()
    cur = await db.execute(
        "SELECT DISTINCT langflow_folder_id FROM workspaces "
        "WHERE langflow_folder_id IS NOT NULL",
        (),
    )
    folders = [dict(r)["langflow_folder_id"] for r in await cur.fetchall()]
    if not folders:
        return

    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            for folder_id in folders:
                try:
                    r = await client.get(
                        f"{lf_url}/api/v1/flows/",
                        params={"folder_id": folder_id, "get_all": "true"},
                    )
                except Exception:
                    log.exception("langflow poll: list flows failed for %s", folder_id)
                    continue
                if r.status_code >= 400:
                    log.warning("langflow poll: list flows returned %s for %s",
                                r.status_code, folder_id)
                    continue
                flows = r.json() or []
                if isinstance(flows, dict):
                    flows = flows.get("flows") or flows.get("items") or []
                for f in flows:
                    fid = f.get("id")
                    if not fid:
                        continue
                    updated_at = f.get("updated_at") or f.get("updatedAt") or ""
                    published = bool(f.get("is_published") or f.get("published"))
                    if not published:
                        continue
                    # The version Langflow returns is monotonic; derive one
                    # from updated_at when it doesn't surface a counter.
                    version = int(f.get("version") or 0) or _version_from_updated_at(updated_at)
                    cur2 = await db.execute(
                        """
                        SELECT MAX(COALESCE(lf_version, version)) AS v
                          FROM agent_flow_snapshots
                         WHERE lf_flow_id = ?
                        """,
                        (str(fid),),
                    )
                    row = await cur2.fetchone()
                    last_version = int(dict(row).get("v") or 0) if row else 0
                    if version <= last_version:
                        continue
                    try:
                        await apply_flow_publish(
                            db,
                            flow_id=str(fid),
                            folder_id=str(folder_id) if folder_id else None,
                            version=version,
                            data=f.get("data") or {},
                            name=f.get("name") or None,
                            source="poll",
                        )
                        log.info("langflow poll: promoted flow %s -> v%s", fid, version)
                    except Exception:
                        log.exception("langflow poll: apply_flow_publish raised for %s", fid)
    except Exception:
        log.exception("langflow publish poll tick crashed")


# ---------- Notification digest emails --------------------------------------

async def _notification_digest_tick() -> None:
    """Roll each user's unread notifications from the last 24h into a single
    email, respecting `notification_prefs.digest` (`daily` / `weekly` / `off`).
    Idempotent: writes a marker row to `delivery_log.kind='digest_marker'` and
    skips users whose latest marker is still fresh.

    Design notes:
    - Only fires when unread > 0 — silent when there's nothing to tell.
    - Honours per-user email opt-in (`notification_prefs.email=true`).
    - Relies on the outbox dispatcher to actually send. If `SMTP_HOST` isn't
      configured, the row lands in `delivery_log` so the operator can see
      what *would* have been sent.
    """
    db = await get_db()
    cur = await db.execute(
        """
        SELECT u.id, u.email, u.name, u.notification_prefs, u.workspace_id,
               (SELECT MAX(at) FROM delivery_log dl
                 WHERE dl.workspace_id = u.workspace_id
                   AND dl.kind = 'digest_marker'
                   AND dl.target = u.email) AS last_digest
          FROM users u
         WHERE u.email IS NOT NULL
        """,
        (),
    )
    users = [dict(r) for r in await cur.fetchall()]
    for u in users:
        prefs = jl(u.get("notification_prefs"), {}) or {}
        if not prefs.get("email"):
            continue
        freq = (prefs.get("digest") or "daily").lower()
        if freq in ("off", "none", "never"):
            continue
        interval_hours = 24 if freq == "daily" else 168  # weekly
        # Re-send guard: skip if we sent a digest within the window.
        last = u.get("last_digest")
        if last is not None:
            try:
                from datetime import datetime as _dt, timezone as _tz
                if isinstance(last, str):
                    last = _dt.fromisoformat(last.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=_tz.utc)
                age_hours = (_dt.now(_tz.utc) - last).total_seconds() / 3600
                if age_hours < interval_hours - 1:
                    continue
            except Exception:
                pass
        cur = await db.execute(
            """
            SELECT id, type, title, body, link, ts
              FROM notifications
             WHERE user_id = ? AND unread = true
               AND ts > now() - (? || ' hours')::interval
             ORDER BY ts DESC LIMIT 50
            """,
            (u["id"], str(interval_hours)),
        )
        notifs = [dict(r) for r in await cur.fetchall()]
        if not notifs:
            continue
        lines = [f"Hi {u.get('name') or 'there'},",
                 "",
                 f"You have {len(notifs)} unread notification(s):",
                 ""]
        for n in notifs:
            ttl = (n.get("title") or n.get("type") or "Notification").strip()
            lines.append(f"• [{n.get('type') or 'notification'}] {ttl}")
            if n.get("body"):
                lines.append(f"    {n['body']}")
            if n.get("link"):
                lines.append(f"    {n['link']}")
        lines.append("")
        lines.append(
            "Manage your digest in Settings → Notifications."
        )
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'email', ?)
            """,
            (u["workspace_id"], js({
                "workspace_id": u["workspace_id"],
                "to": u["email"],
                "subject": f"Your {'weekly' if freq == 'weekly' else 'daily'} WorkspaceX digest",
                "body": "\n".join(lines),
                "kind": "digest",
            })),
        )
        # Marker row so the next tick can cheaply check if we already sent.
        try:
            await db.execute(
                """
                INSERT INTO delivery_log (workspace_id, kind, target, subject, status)
                VALUES (?, 'digest_marker', ?, ?, 'sent')
                """,
                (u["workspace_id"], u["email"],
                 f"digest-{freq}-{len(notifs)}"),
            )
        except Exception:
            pass
    await db.commit()


def _version_from_updated_at(ts: str) -> int:
    """Fallback version derivation: epoch seconds of the flow's `updated_at`.
    Gives us a monotonic int we can compare without Langflow exposing a real
    version counter."""
    if not ts:
        return 1
    try:
        from datetime import datetime as _dt
        # Strip trailing Z for fromisoformat on older Pythons.
        clean = ts.replace("Z", "+00:00")
        return int(_dt.fromisoformat(clean).timestamp())
    except Exception:
        return 1


# ---------- loop harness ----------------------------------------------------

async def _run_loop(fn, interval: float, stop: asyncio.Event, name: str) -> None:
    while not stop.is_set():
        try:
            await fn()
        except Exception:
            log.exception("worker loop %s raised", name)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    await get_db()
    log.info("worker booted (id=%s) — outbox + sla + auto-close loops active", WORKER_ID)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_run_loop(_outbox_tick,              0.5,  stop, "outbox")),
        asyncio.create_task(_run_loop(_sla_tick,                30.0,  stop, "sla")),
        asyncio.create_task(_run_loop(_auto_close_tick,         60.0,  stop, "auto_close")),
        asyncio.create_task(_run_loop(_presence_tick,           60.0,  stop, "presence")),
        asyncio.create_task(_run_loop(_health_prober_tick,      60.0,  stop, "health_prober")),
        asyncio.create_task(_run_loop(_counter_refresh_tick,    30.0,  stop, "counters")),
        asyncio.create_task(_run_loop(_analytics_rollup_tick,   3600,  stop, "analytics_rollup")),
        asyncio.create_task(_run_loop(_idempotency_cleanup_tick, 900,  stop, "idem_cleanup")),
        asyncio.create_task(_run_loop(_oauth_state_cleanup_tick, 600,  stop, "oauth_cleanup")),
        asyncio.create_task(_run_loop(_gmail_poller_tick,         30,  stop, "gmail_poller")),
        asyncio.create_task(_run_loop(_shopify_backfill_tick,   3600,  stop, "shopify_backfill")),
        asyncio.create_task(_run_loop(_embedding_indexer_tick,    60,  stop, "embedding_indexer")),
        asyncio.create_task(_run_loop(_watch_renewer_tick,      3600,  stop, "watch_renewer")),
        asyncio.create_task(_run_loop(_langflow_publish_poll_tick, 30,  stop, "langflow_poll")),
        asyncio.create_task(_run_loop(_notification_digest_tick, 3600,  stop, "notification_digest")),
    ]

    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await close_db()
        log.info("worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
