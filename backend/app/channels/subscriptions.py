"""Channel push-subscription lifecycle (spec §11 watch renewer).

Keeps three provider-side subscriptions alive:

- **Gmail `users.watch`** (expires 7 d). Re-issue every 6 d; notify the admin
  on `invalid_grant` so they reconnect.
- **Outlook Graph `/subscriptions`** (expires 3 d). Re-issue every 24 h or
  whenever `expirationDateTime` is less than 24 h away.
- **Shopify webhook topics**. The app needs one registered webhook per topic
  it cares about (`orders/create`, `refunds/create`, …). The reconciler lists
  the shop's current registrations and POSTs any missing ones.

State lives in `channel_subscriptions`. Errors surface through a
`reauth_required` notification on the installing user and flip the
credential row to `status='reauth_required'` so the Platforms page can
show the "Reconnect" chip.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from ..db import get_db, jl, js
from ..routers.notifications import create_notification

log = logging.getLogger(__name__)


SHOPIFY_WEBHOOK_TOPICS = [
    "orders/create",
    "orders/updated",
    "orders/cancelled",
    "orders/fulfilled",
    "refunds/create",
    "products/update",
]


# ---------- helpers --------------------------------------------------------

async def _flag_reauth(db, cred_row: dict, provider: str, reason: str) -> None:
    """Flip the credential to `reauth_required` and push a notification so the
    admin sees the outage promptly."""
    await db.execute(
        "UPDATE platform_credentials SET status = 'reauth_required' WHERE id = ?",
        (cred_row["id"],),
    )
    await db.commit()
    if cred_row.get("installed_by"):
        try:
            await create_notification(
                cred_row["installed_by"], "reauth",
                title=f"Reconnect {provider.title()}",
                body=reason,
                link="/platforms",
            )
        except Exception:
            log.exception("reauth notification failed")


async def _record_subscription(db, *, workspace_id: str, credential_id: str,
                                provider: str, topic: str | None,
                                provider_sub_id: str | None,
                                history_id: str | None,
                                expires_at: str | None,
                                status: str = "active",
                                error: str | None = None,
                                meta: dict | None = None) -> None:
    """Upsert the subscription row keyed by (credential, provider, topic)."""
    # Look up an existing row to re-key the renewer.
    cur = await db.execute(
        """
        SELECT id FROM channel_subscriptions
         WHERE credential_id = ? AND provider = ? AND COALESCE(topic, '') = COALESCE(?, '')
         LIMIT 1
        """,
        (credential_id, provider, topic or ""),
    )
    existing = await cur.fetchone()
    if existing:
        await db.execute(
            """
            UPDATE channel_subscriptions
               SET provider_sub_id = ?, history_id = ?, expires_at = ?,
                   status = ?, error = ?, last_renewed_at = now(), meta = ?
             WHERE id = ?
            """,
            (provider_sub_id, history_id, expires_at, status, error,
             js(meta or {}), existing["id"]),
        )
    else:
        await db.execute(
            """
            INSERT INTO channel_subscriptions
              (workspace_id, credential_id, provider, topic, provider_sub_id,
               history_id, expires_at, status, error, last_renewed_at, meta)
            VALUES (?,?,?,?,?,?,?,?,?, now(), ?)
            """,
            (workspace_id, credential_id, provider, topic, provider_sub_id,
             history_id, expires_at, status, error, js(meta or {})),
        )
    await db.commit()


# ---------- Gmail users.watch ---------------------------------------------

async def renew_gmail_watch(db, cred_row: dict) -> None:
    """Issue / refresh `users.watch`. Requires a Pub/Sub topic name; without
    `GMAIL_PUBSUB_TOPIC` set we skip, since calling watch without a topic
    returns 400. The poller keeps ingesting either way."""
    topic = os.environ.get("GMAIL_PUBSUB_TOPIC")
    if not topic:
        log.debug("gmail watch skipped — GMAIL_PUBSUB_TOPIC not set")
        return
    secrets = jl(cred_row.get("secrets"), {}) or {}
    token = secrets.get("access_token")
    refresh = secrets.get("refresh_token")
    if not token or token.startswith("dev_"):
        return
    try:
        import httpx  # type: ignore
    except ImportError:
        return

    async def _do_watch(access_token: str) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/watch",
                headers={"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json"},
                json={"topicName": topic, "labelIds": ["INBOX"]},
            )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return resp.status_code, body

    status_code, body = await _do_watch(token)
    if status_code == 401 and refresh:
        from .gmail_poller import _refresh_access_token
        new_token = await _refresh_access_token(db, cred_row["id"], refresh)
        if new_token:
            status_code, body = await _do_watch(new_token)
    if status_code >= 400:
        err = f"HTTP {status_code}: {str(body)[:200]}"
        await _record_subscription(
            db, workspace_id=cred_row["workspace_id"],
            credential_id=cred_row["id"], provider="gmail",
            topic=topic, provider_sub_id=None, history_id=None,
            expires_at=None, status="error", error=err,
        )
        if status_code in (401, 403):
            await _flag_reauth(db, cred_row, "gmail",
                               "Gmail push subscription failed — reconnect to resume realtime.")
        return
    # Gmail returns `{historyId, expiration}` (ms since epoch).
    info = body if isinstance(body, dict) else {}
    expiration = info.get("expiration")
    exp_iso = None
    if expiration:
        try:
            exp_iso = datetime.fromtimestamp(int(expiration) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            exp_iso = None
    await _record_subscription(
        db, workspace_id=cred_row["workspace_id"],
        credential_id=cred_row["id"], provider="gmail",
        topic=topic, provider_sub_id=None,
        history_id=str(info.get("historyId") or ""),
        expires_at=exp_iso, status="active",
    )


# ---------- Outlook Graph /subscriptions ----------------------------------

async def renew_outlook_subscription(db, cred_row: dict) -> None:
    """Create or renew a Graph `/subscriptions` for `/me/mailFolders('inbox')/messages`.
    Graph max lifetime is 4230 min (~70 h) for mail; we aim for 70 h and the
    renewer loop re-extends ~daily."""
    secrets = jl(cred_row.get("secrets"), {}) or {}
    token = secrets.get("access_token")
    if not token or token.startswith("dev_"):
        return
    try:
        import httpx  # type: ignore
    except ImportError:
        return

    notification_url = os.environ.get("OUTLOOK_NOTIFICATION_URL")
    if not notification_url:
        return
    client_state = os.environ.get("OUTLOOK_CLIENT_STATE") or "workspacex"
    new_expiry = (datetime.now(timezone.utc) + timedelta(minutes=4200)).isoformat().replace("+00:00", "Z")

    # Look up an existing row first so we issue PATCH /subscriptions/<id>
    # instead of creating a new one every loop.
    cur = await db.execute(
        """
        SELECT * FROM channel_subscriptions
         WHERE credential_id = ? AND provider = 'outlook' AND status = 'active'
         LIMIT 1
        """,
        (cred_row["id"],),
    )
    existing = await cur.fetchone()
    sub_id = existing["provider_sub_id"] if existing else None

    async def _post(url: str, method: str, json_body: dict) -> tuple[int, dict | str]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.request(
                method, url,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=json_body,
            )
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        return resp.status_code, body

    if sub_id:
        status, body = await _post(
            f"https://graph.microsoft.com/v1.0/subscriptions/{sub_id}",
            "PATCH", {"expirationDateTime": new_expiry},
        )
        if status == 404:
            sub_id = None  # fall through to create

    if not sub_id:
        status, body = await _post(
            "https://graph.microsoft.com/v1.0/subscriptions",
            "POST",
            {
                "changeType": "created",
                "notificationUrl": notification_url,
                "resource": "/me/mailFolders('inbox')/messages",
                "expirationDateTime": new_expiry,
                "clientState": client_state,
            },
        )
        if status < 400 and isinstance(body, dict):
            sub_id = body.get("id")

    if status >= 400:
        err = f"HTTP {status}: {str(body)[:200]}"
        await _record_subscription(
            db, workspace_id=cred_row["workspace_id"],
            credential_id=cred_row["id"], provider="outlook",
            topic="inbox_messages", provider_sub_id=sub_id,
            history_id=None, expires_at=None, status="error", error=err,
        )
        if status in (401, 403):
            await _flag_reauth(db, cred_row, "outlook",
                               "Outlook mailbox subscription failed — reconnect to resume email sync.")
        return

    await _record_subscription(
        db, workspace_id=cred_row["workspace_id"],
        credential_id=cred_row["id"], provider="outlook",
        topic="inbox_messages", provider_sub_id=sub_id,
        history_id=None, expires_at=new_expiry, status="active",
    )


# ---------- Shopify webhook topic reconciler ------------------------------

async def reconcile_shopify_topics(db, cred_row: dict) -> None:
    """Make sure every topic in `SHOPIFY_WEBHOOK_TOPICS` has a webhook
    registered against `BACKEND_ORIGIN/webhooks/shopify`. Shopify dedupes on
    (topic, address) so re-registering is idempotent."""
    secrets = jl(cred_row.get("secrets"), {}) or {}
    token = secrets.get("access_token")
    meta = jl(cred_row.get("meta"), {}) or {}
    shop = meta.get("shop") or cred_row.get("account_id")
    if not token or not shop:
        return
    address = os.environ.get("SHOPIFY_WEBHOOK_ADDRESS")
    if not address:
        return
    try:
        import httpx  # type: ignore
    except ImportError:
        return

    api_base = f"https://{shop}/admin/api/2024-01"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        resp = await client.get(f"{api_base}/webhooks.json")
        if resp.status_code >= 400:
            await _record_subscription(
                db, workspace_id=cred_row["workspace_id"],
                credential_id=cred_row["id"], provider="shopify",
                topic="__list__", provider_sub_id=None, history_id=None,
                expires_at=None, status="error",
                error=f"list HTTP {resp.status_code}",
            )
            if resp.status_code in (401, 403):
                await _flag_reauth(db, cred_row, "shopify",
                                   "Shopify webhook registration failed — reconnect the store.")
            return
        existing = resp.json().get("webhooks") or []
        have = {(w.get("topic"), w.get("address")) for w in existing}

        for topic in SHOPIFY_WEBHOOK_TOPICS:
            if (topic, address) in have:
                continue
            create = await client.post(
                f"{api_base}/webhooks.json",
                json={"webhook": {"topic": topic, "address": address,
                                  "format": "json"}},
            )
            if create.status_code >= 400:
                log.warning("shopify webhook create failed (%s): %s",
                            create.status_code, create.text[:200])
                continue
            body = create.json().get("webhook") or {}
            await _record_subscription(
                db, workspace_id=cred_row["workspace_id"],
                credential_id=cred_row["id"], provider="shopify",
                topic=topic, provider_sub_id=str(body.get("id") or ""),
                history_id=None,
                expires_at=None,  # Shopify webhooks don't expire
                status="active",
            )


# ---------- renewer tick --------------------------------------------------

async def watch_renewer_tick() -> None:
    """Invoked hourly by the worker. Sweeps `platform_credentials` for active
    Gmail / Outlook / Shopify installs and brings their push subscription up
    to date. Bounded at 20 credentials per tick to keep runtime predictable."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT * FROM platform_credentials
         WHERE status = 'active' AND provider IN ('gmail','outlook','shopify')
         ORDER BY created_at ASC LIMIT 20
        """,
        (),
    )
    creds = [dict(r) for r in await cur.fetchall()]
    for c in creds:
        try:
            if c["provider"] == "gmail":
                # Gmail watch lasts 7d; renew once the stored expiry is < 24h away.
                if await _needs_renewal(db, c["id"], "gmail", hours_before=24):
                    await renew_gmail_watch(db, c)
            elif c["provider"] == "outlook":
                # Outlook mailbox subs last 70h; renew < 12h out.
                if await _needs_renewal(db, c["id"], "outlook", hours_before=12):
                    await renew_outlook_subscription(db, c)
            elif c["provider"] == "shopify":
                # Shopify webhooks are permanent; reconcile daily.
                if await _needs_renewal(db, c["id"], "shopify", hours_before=23):
                    await reconcile_shopify_topics(db, c)
        except Exception:
            log.exception("subscription renewal failed (cred=%s provider=%s)",
                          c.get("id"), c.get("provider"))


async def _needs_renewal(db, credential_id: str, provider: str,
                         hours_before: int) -> bool:
    """Return True when no row exists yet or the stored expiry is within
    `hours_before` of now. Providers without `expires_at` (Shopify) use
    `last_renewed_at` instead."""
    cur = await db.execute(
        """
        SELECT expires_at, last_renewed_at FROM channel_subscriptions
         WHERE credential_id = ? AND provider = ? AND status = 'active'
         ORDER BY last_renewed_at DESC NULLS LAST LIMIT 1
        """,
        (credential_id, provider),
    )
    r = await cur.fetchone()
    if not r:
        return True
    row = dict(r)
    threshold = datetime.now(timezone.utc) + timedelta(hours=hours_before)
    exp = row.get("expires_at")
    if exp:
        if isinstance(exp, str):
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except Exception:
                return True
        else:
            exp_dt = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        return exp_dt <= threshold
    last = row.get("last_renewed_at")
    if isinstance(last, str):
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except Exception:
            return True
    elif last is None:
        return True
    else:
        last_dt = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_dt) > timedelta(hours=hours_before)
