"""Platforms (spec §3.8)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Query, Request

from ..channels.connectors import (
    CONNECTORS,
    mask_secret,
    schema_for,
    split_payload,
    validate_values,
)
from ..db import get_db, jl, js
from ..deps import Me

log = logging.getLogger(__name__)

router = APIRouter(tags=["platforms"])


# Default integration catalog. The front-end merges workspace state in
# by `id`, so any row stored in the `platforms` table with a matching id
# will override `status` / `tickets` here.
DEFAULT_CATALOG: list[dict] = [
    # --- Messaging ---
    {
        "id": "whatsapp",
        "kind": "messaging",
        "group": "messaging",
        "name": "WhatsApp Business",
        "category": "Messaging",
        "desc": "Two-way messaging, templates and broadcasts via the WhatsApp Business API.",
        "letter": "W",
        "accent": "linear-gradient(135deg,#25D366,#128C7E)",
    },
    {
        "id": "instagram",
        "kind": "messaging",
        "group": "messaging",
        "name": "Instagram DM",
        "category": "Messaging",
        "desc": "Capture DMs, story replies and mentions from your Instagram profile.",
        "letter": "I",
        "accent": "linear-gradient(135deg,#F58529,#DD2A7B,#8134AF)",
    },
    {
        "id": "messenger",
        "kind": "messaging",
        "group": "messaging",
        "name": "Facebook Messenger",
        "category": "Messaging",
        "desc": "Route conversations from your Facebook page inbox into tickets.",
        "letter": "M",
        "accent": "linear-gradient(135deg,#00B2FF,#006AFF)",
    },
    {
        "id": "telegram",
        "kind": "messaging",
        "group": "messaging",
        "name": "Telegram",
        "category": "Messaging",
        "desc": "Connect a Telegram bot to receive and reply to customer chats.",
        "letter": "T",
        "accent": "linear-gradient(135deg,#2AABEE,#229ED9)",
    },
    {
        "id": "gmail",
        "kind": "email",
        "group": "messaging",
        "name": "Gmail",
        "category": "Email",
        "desc": "Sync a Google Workspace or personal Gmail inbox as a support channel.",
        "letter": "G",
        "accent": "linear-gradient(135deg,#EA4335,#FBBC04)",
    },
    {
        "id": "outlook",
        "kind": "email",
        "group": "messaging",
        "name": "Outlook / Microsoft 365",
        "category": "Email",
        "desc": "Pull mail from an Outlook or Microsoft 365 mailbox into shared inboxes.",
        "letter": "O",
        "accent": "linear-gradient(135deg,#0078D4,#50E6FF)",
    },
    {
        "id": "imap-smtp",
        "kind": "email",
        "group": "messaging",
        "name": "IMAP / SMTP",
        "category": "Email",
        "desc": "Connect any mailbox using standard IMAP & SMTP credentials.",
        "letter": "E",
        "accent": "linear-gradient(135deg,#475569,#0f172a)",
    },
    {
        "id": "sms-twilio",
        "kind": "messaging",
        "group": "messaging",
        "name": "SMS (Twilio)",
        "category": "Messaging",
        "desc": "Send and receive SMS through your Twilio number.",
        "letter": "S",
        "accent": "linear-gradient(135deg,#F22F46,#B6002A)",
    },
    {
        "id": "live-chat",
        "kind": "messaging",
        "group": "messaging",
        "name": "Live Chat Widget",
        "category": "Messaging",
        "desc": "Embed a chat widget on your website for real-time customer support.",
        "letter": "L",
        "accent": "linear-gradient(135deg,#6366F1,#22D3EE)",
    },
    # --- E-commerce ---
    {
        "id": "shopify",
        "kind": "ecommerce",
        "group": "ecommerce",
        "name": "Shopify",
        "category": "E-commerce",
        "desc": "Pull orders, customers and fulfilment status from your Shopify store.",
        "letter": "S",
        "accent": "linear-gradient(135deg,#95BF47,#5E8E3E)",
    },
    {
        "id": "woocommerce",
        "kind": "ecommerce",
        "group": "ecommerce",
        "name": "WooCommerce",
        "category": "E-commerce",
        "desc": "Sync a WooCommerce storefront over the REST API.",
        "letter": "W",
        "accent": "linear-gradient(135deg,#7F54B3,#96588A)",
    },
    {
        "id": "magento",
        "kind": "ecommerce",
        "group": "ecommerce",
        "name": "Magento / Adobe Commerce",
        "category": "E-commerce",
        "desc": "Connect Magento 2 stores for orders and customer lookups.",
        "letter": "M",
        "accent": "linear-gradient(135deg,#EE672F,#F26322)",
    },
    {
        "id": "amazon",
        "kind": "ecommerce",
        "group": "ecommerce",
        "name": "Amazon Seller Central",
        "category": "Marketplace",
        "desc": "Handle Amazon orders, returns and buyer messages in one place.",
        "letter": "A",
        "accent": "linear-gradient(135deg,#FF9900,#232F3E)",
    },
    {
        "id": "flipkart",
        "kind": "ecommerce",
        "group": "ecommerce",
        "name": "Flipkart Seller Hub",
        "category": "Marketplace",
        "desc": "Monitor Flipkart orders and SLA timers from the shared inbox.",
        "letter": "F",
        "accent": "linear-gradient(135deg,#2874F0,#F8D442)",
    },
    # --- Shipping partners ---
    {
        "id": "shiprocket",
        "kind": "shipping",
        "group": "shipping",
        "name": "Shiprocket",
        "category": "Shipping",
        "desc": "Aggregator integration for AWBs, manifests and RTO updates.",
        "letter": "S",
        "accent": "linear-gradient(135deg,#7B2CBF,#9D4EDD)",
    },
    {
        "id": "delhivery",
        "kind": "shipping",
        "group": "shipping",
        "name": "Delhivery",
        "category": "Shipping",
        "desc": "Track Delhivery shipments and trigger pickups from tickets.",
        "letter": "D",
        "accent": "linear-gradient(135deg,#E03A3E,#8B0000)",
    },
    {
        "id": "bluedart",
        "kind": "shipping",
        "group": "shipping",
        "name": "Blue Dart",
        "category": "Shipping",
        "desc": "Connect Blue Dart for air and surface shipment tracking.",
        "letter": "B",
        "accent": "linear-gradient(135deg,#003366,#0066CC)",
    },
    {
        "id": "fedex",
        "kind": "shipping",
        "group": "shipping",
        "name": "FedEx",
        "category": "Shipping",
        "desc": "International shipping tracking via FedEx Web Services.",
        "letter": "F",
        "accent": "linear-gradient(135deg,#4D148C,#FF6600)",
    },
    {
        "id": "dhl",
        "kind": "shipping",
        "group": "shipping",
        "name": "DHL Express",
        "category": "Shipping",
        "desc": "Worldwide parcel tracking with DHL Express API.",
        "letter": "D",
        "accent": "linear-gradient(135deg,#FFCC00,#D40511)",
    },
]


_CATALOG_BY_ID = {e["id"]: e for e in DEFAULT_CATALOG}

# OAuth-backed channels. `platforms.status` stays sticky until the user
# explicitly disconnects; platform_credentials tell us whether the stored
# tokens are still usable, which we surface as a separate `needs_reauth` flag
# so the UI can nudge the admin to reconnect without flipping the card.
_OAUTH_CHANNEL_PROVIDERS = {"gmail": "gmail", "outlook": "outlook"}


async def _workspace_overrides(workspace_id: str) -> dict[str, dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM platforms WHERE workspace_id = ?", (workspace_id,)
    )
    out: dict[str, dict] = {}
    for r in [dict(x) for x in await cur.fetchall()]:
        out[r["id"]] = r
    return out


async def _oauth_credential_health(workspace_id: str) -> dict[str, dict]:
    """Per-channel credential health. Returns `{has_active, needs_reauth}` for
    each OAuth-backed platform id with any credential row in the workspace."""
    db = await get_db()
    providers = tuple(_OAUTH_CHANNEL_PROVIDERS.values())
    placeholders = ",".join(["?"] * len(providers))
    cur = await db.execute(
        f"""
        SELECT provider, status FROM platform_credentials
         WHERE workspace_id = ? AND provider IN ({placeholders})
        """,
        (workspace_id, *providers),
    )
    by_provider: dict[str, set[str]] = {}
    for row in await cur.fetchall():
        r = dict(row)
        by_provider.setdefault(r["provider"], set()).add(r["status"])
    out: dict[str, dict] = {}
    for pid, provider in _OAUTH_CHANNEL_PROVIDERS.items():
        statuses = by_provider.get(provider) or set()
        out[pid] = {
            "has_active": "active" in statuses,
            "needs_reauth": (bool(statuses)
                             and "active" not in statuses
                             and "reauth_required" in statuses),
        }
    return out


def _needs_reauth_for(pid: str, status: str,
                      health: dict[str, dict]) -> bool:
    """True when the card should show a reauth badge: the user has it marked
    connected, but no usable credential is available to back it."""
    if pid not in _OAUTH_CHANNEL_PROVIDERS or status != "connected":
        return False
    h = health.get(pid) or {}
    return not h.get("has_active", False)


@router.get("/platforms")
async def list_platforms(
    include: str | None = Query(None),
    user: dict = Me,
) -> list[dict]:
    overrides = await _workspace_overrides(user["workspace_id"])
    oauth_health = await _oauth_credential_health(user["workspace_id"])

    if include == "all":
        merged: list[dict] = []
        for entry in DEFAULT_CATALOG:
            ov = overrides.get(entry["id"])
            status = (ov or {}).get("status", "available")
            merged.append({
                **entry,
                "status": status,
                "needs_reauth": _needs_reauth_for(entry["id"], status, oauth_health),
                "tickets": (ov or {}).get("tickets_count", 0),
                "cls": entry["kind"].lower(),
                "_group": entry["group"],
            })
        # Include any workspace-specific platforms that aren't in the default catalog.
        for pid, ov in overrides.items():
            if pid in _CATALOG_BY_ID:
                continue
            merged.append({
                "id": pid,
                "name": ov["name"],
                "desc": ov.get("description"),
                "kind": ov["kind"],
                "status": ov["status"],
                "tickets": ov["tickets_count"],
                "cls": ov["kind"].lower(),
                "category": ov["kind"].title(),
                "_group": ov["kind"].lower(),
                "letter": (ov["name"] or "?")[:1].upper(),
                "accent": "linear-gradient(135deg,#475569,#0f172a)",
            })
        return merged

    # Default: only the workspace's platforms (legacy behaviour).
    out = []
    for pid, r in overrides.items():
        entry = _CATALOG_BY_ID.get(pid)
        out.append({
            "id": pid,
            "name": r["name"],
            "desc": r.get("description"),
            "kind": r["kind"],
            "status": r["status"],
            "needs_reauth": _needs_reauth_for(pid, r["status"], oauth_health),
            "tickets": r["tickets_count"],
            "cls": r["kind"].lower(),
            "category": (entry or {}).get("category", r["kind"].title()),
            "letter": (entry or {}).get("letter", (r["name"] or "?")[:1].upper()),
            "accent": (entry or {}).get("accent", "linear-gradient(135deg,#475569,#0f172a)"),
        })
    out.sort(key=lambda x: x["name"])
    return out


@router.get("/platforms/stats")
async def platforms_stats(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM platforms WHERE workspace_id = ? AND status = 'connected'",
        (user["workspace_id"],),
    )
    connected = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM tickets WHERE workspace_id = ? AND created_at::date = current_date",
        (user["workspace_id"],),
    )
    tickets_today = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM platforms WHERE workspace_id = ? AND status = 'pending'",
        (user["workspace_id"],),
    )
    pending = (await cur.fetchone())[0] or 0
    return {
        "connected": int(connected),
        "ticketsToday": int(tickets_today),
        "pending": int(pending),
        "avgResponse": "2h 14m",
        "connectedTrend": "+1",
        "ticketsTrend": "+12%",
        "avgResponseTrend": "-8%",
    }


async def _set_status(workspace_id: str, pid: str, status: str) -> None:
    entry = _CATALOG_BY_ID.get(pid)
    if not entry:
        raise HTTPException(404, "platform not found")
    db = await get_db()
    await db.execute(
        """
        INSERT INTO platforms (id, workspace_id, kind, name, description, status, tickets_count, config)
        VALUES (?,?,?,?,?,?,0,'{}')
        ON CONFLICT (workspace_id, id) DO UPDATE SET status = EXCLUDED.status
        """,
        (pid, workspace_id, entry["kind"], entry["name"], entry["desc"], status),
    )
    await db.commit()


@router.post("/platforms/{pid}/connect")
async def connect(pid: str, user: dict = Me) -> dict:
    await _set_status(user["workspace_id"], pid, "connected")
    return {"ok": True, "status": "connected"}


@router.post("/platforms/{pid}/disconnect")
async def disconnect(pid: str, user: dict = Me) -> dict:
    await _set_status(user["workspace_id"], pid, "disconnected")
    # OAuth *and* API-key channels key off platform_credentials. Deactivate
    # the stored tokens too, otherwise the poller keeps hitting a "disconnected"
    # mailbox and list_platforms would flip the card back to connected on next
    # read. Covers gmail/outlook (OAuth) as well as whatsapp/telegram/imap
    # (API-key) — anything that landed in platform_credentials under a known
    # provider slug.
    providers: set[str] = set()
    if pid in _OAUTH_CHANNEL_PROVIDERS:
        providers.add(_OAUTH_CHANNEL_PROVIDERS[pid])
    conn_cfg = CONNECTORS.get(pid)
    if conn_cfg:
        providers.add(conn_cfg["provider"])
    if providers:
        db = await get_db()
        placeholders = ",".join(["?"] * len(providers))
        await db.execute(
            f"""
            UPDATE platform_credentials SET status = 'revoked'
             WHERE workspace_id = ? AND provider IN ({placeholders})
            """,
            (user["workspace_id"], *providers),
        )
        await db.commit()
    return {"ok": True, "status": "disconnected"}


# ---------------------------------------------------------------------------
# Connector credential schema + configure endpoints
# ---------------------------------------------------------------------------
# These two routes drive the "Connect" modal on the Channels page for any
# non-OAuth provider. The UI asks `GET /platforms/:id/schema` for the field
# list, renders a form, and POSTs `{credentials: {...}}` back here. We write
# the values into `platform_credentials` so the adapters in worker.py + the
# pollers under channels/ pick them up without the operator editing .env.


# OAuth-managed platforms have no credential form — the modal renders an
# account summary + sync controls instead. Map keeps the UI in sync with the
# provider/scopes used in routers/oauth.py.
_OAUTH_PLATFORM_INFO = {
    "gmail": {
        "provider": "gmail",
        "oauth_provider": "google",
        "scopes_summary": "Read + send mail (gmail.modify, gmail.send)",
        "supports_sync": True,
        "docs_url": "https://developers.google.com/gmail/api/auth/scopes",
    },
    "outlook": {
        "provider": "outlook",
        "oauth_provider": "outlook",
        "scopes_summary": "Read + send mail (Mail.Read, Mail.Send, Mail.ReadWrite)",
        "supports_sync": True,
        "docs_url": "https://learn.microsoft.com/graph/permissions-reference",
    },
}


async def _oauth_account_summary(workspace_id: str, pid: str) -> dict | None:
    """Account-level details the Manage modal renders for OAuth-backed cards:
    linked email, when it was connected, when the access token expires, and
    whether the stored refresh token is still healthy."""
    info = _OAUTH_PLATFORM_INFO.get(pid)
    if not info:
        return None
    db = await get_db()
    cur = await db.execute(
        """
        SELECT account_id, meta, status, expires_at, created_at, installed_by
          FROM platform_credentials
         WHERE workspace_id = ? AND provider = ?
         ORDER BY (status = 'active') DESC, created_at DESC
         LIMIT 1
        """,
        (workspace_id, info["provider"]),
    )
    r = await cur.fetchone()
    if not r:
        return {
            "kind": "oauth",
            "provider": info["provider"],
            "oauth_provider": info["oauth_provider"],
            "scopes_summary": info["scopes_summary"],
            "supports_sync": info["supports_sync"],
            "docs_url": info["docs_url"],
            "connected": False,
        }
    row = dict(r)
    meta = jl(row.get("meta"), {}) or {}
    installer_email = None
    if row.get("installed_by"):
        cur2 = await db.execute(
            "SELECT email, name FROM users WHERE id = ?", (row["installed_by"],),
        )
        u = await cur2.fetchone()
        if u:
            ud = dict(u)
            installer_email = ud.get("email")
    return {
        "kind": "oauth",
        "provider": info["provider"],
        "oauth_provider": info["oauth_provider"],
        "scopes_summary": info["scopes_summary"],
        "supports_sync": info["supports_sync"],
        "docs_url": info["docs_url"],
        "connected": row.get("status") == "active",
        "needs_reauth": row.get("status") == "reauth_required",
        "linked_email": meta.get("email") or row.get("account_id"),
        "linked_name": meta.get("name"),
        "linked_sub": meta.get("sub"),
        "account_id": row.get("account_id"),
        "expires_at": row.get("expires_at"),
        "connected_at": row.get("created_at"),
        "installed_by_email": installer_email,
    }


@router.get("/platforms/{pid}/schema")
async def platform_schema(pid: str, request: Request, user: dict = Me) -> dict:
    """Return the credential form schema for this platform.

    Three response shapes:
    - **OAuth-managed** (Gmail/Outlook): `{kind: "oauth", connected, linked_email, …}`.
      The modal renders a status panel with sync + reconnect buttons.
    - **Form-driven** (everything else in CONNECTORS): the field list + any
      stored values masked.
    - **404**: unknown platform.
    """
    backend_base = os.environ.get("BACKEND_ORIGIN") or str(request.base_url).rstrip("/")
    if pid in _OAUTH_PLATFORM_INFO:
        return await _oauth_account_summary(user["workspace_id"], pid) or {}

    sch = schema_for(pid)
    if not sch:
        raise HTTPException(404, "connector not found")
    if sch.get("webhook_instructions"):
        sch["webhook_instructions"] = sch["webhook_instructions"].replace(
            "{backend}", backend_base,
        )
    # Surface the current stored values (masked) + account id so the modal
    # can show "Reconfigure" instead of blank fields on re-open.
    existing = await _load_credential_for_ui(user["workspace_id"], pid)
    if existing:
        sch["existing"] = existing
    # Live chat: inject the workspace slug so the UI can render the embed snippet.
    if pid == "live-chat":
        db = await get_db()
        cur = await db.execute(
            "SELECT slug FROM workspaces WHERE id = ?", (user["workspace_id"],),
        )
        row = await cur.fetchone()
        slug = dict(row)["slug"] if row else None
        sch["workspace_slug"] = slug
        sch["embed_url"] = f"{backend_base}/widget.js?workspace={slug}" if slug else None
    return sch


@router.post("/platforms/{pid}/configure")
async def configure_platform(
    pid: str,
    request: Request,
    body: dict = Body(...),
    user: dict = Me,
) -> dict:
    """Save the credential values the user entered in the modal and flip the
    card to `connected`. The adapter layer (worker.py `_send_*`, channels/*
    pollers) reads from `platform_credentials` on every send/tick, so the
    connector becomes functional the moment this returns."""
    cfg = CONNECTORS.get(pid)
    if not cfg:
        raise HTTPException(404, "connector not found")
    values = (body or {}).get("credentials") or {}
    if not isinstance(values, dict):
        raise HTTPException(400, "credentials must be an object")
    # Apply defaults from the schema so required fields like imap_host /
    # smtp_port don't need to be retyped when the user just wants Gmail IMAP.
    for f in cfg.get("fields") or []:
        k = f["key"]
        if (values.get(k) in (None, "")) and f.get("default") is not None:
            values[k] = f["default"]
    errors = validate_values(pid, values)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    secrets, meta, account_id = split_payload(pid, values)
    provider = cfg["provider"]
    ws_id = user["workspace_id"]
    db = await get_db()

    # Upsert the credential row. Account_id fallback: the provider slug itself
    # so single-account connectors (twilio, imap) don't need one.
    acct = account_id or provider
    expires_at = None
    await db.execute(
        """
        INSERT INTO platform_credentials (workspace_id, provider, account_id,
                                          status, secrets, meta, installed_by, expires_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT (workspace_id, provider, account_id) DO UPDATE SET
          secrets = EXCLUDED.secrets, meta = EXCLUDED.meta,
          status = 'active', expires_at = EXCLUDED.expires_at,
          installed_by = EXCLUDED.installed_by
        """,
        (ws_id, provider, acct, "active", js(secrets), js(meta), user["id"], expires_at),
    )
    # Flip the card to connected in `platforms` so the UI updates in place.
    entry = _CATALOG_BY_ID.get(pid) or {
        "kind": "messaging", "name": pid.title(),
        "desc": f"{pid} connector",
    }
    await db.execute(
        """
        INSERT INTO platforms (id, workspace_id, kind, name, description,
                               status, tickets_count, config)
        VALUES (?,?,?,?,?, 'connected', 0, ?)
        ON CONFLICT (workspace_id, id) DO UPDATE SET
          status = 'connected', config = EXCLUDED.config
        """,
        (pid, ws_id, entry["kind"], entry["name"], entry["desc"], js(meta)),
    )
    await db.commit()

    # For Telegram we can register a webhook as a one-time setup so messages
    # flow even without the polling loop — best effort, not fatal.
    if provider == "telegram":
        try:
            from ..channels.telegram_poller import register_webhook_if_configured
            request_origin = os.environ.get("BACKEND_ORIGIN") or str(request.base_url).rstrip("/")
            await register_webhook_if_configured(
                bot_token=secrets.get("bot_token"),
                webhook_base=request_origin,
                webhook_secret=meta.get("webhook_secret") or secrets.get("webhook_secret"),
            )
        except Exception:
            log.exception("telegram webhook register failed")

    return {"ok": True, "status": "connected",
            "existing": await _load_credential_for_ui(ws_id, pid)}


async def _load_credential_for_ui(workspace_id: str, pid: str) -> dict | None:
    """Return a safe-to-render representation of the stored credential: the
    account id, the meta blob, and every secret key masked with `••••tail`.
    Nothing sensitive leaves the backend — the modal just uses this to show
    which fields have a value so the admin can edit selectively."""
    cfg = CONNECTORS.get(pid)
    if not cfg:
        return None
    provider = cfg["provider"]
    db = await get_db()
    cur = await db.execute(
        """
        SELECT account_id, secrets, meta, status, expires_at, created_at
          FROM platform_credentials
         WHERE workspace_id = ? AND provider = ? AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (workspace_id, provider),
    )
    r = await cur.fetchone()
    if not r:
        return None
    row = dict(r)
    secrets_blob = jl(row.get("secrets"), {}) or {}
    meta = jl(row.get("meta"), {}) or {}
    masked = {k: mask_secret(v) for k, v in secrets_blob.items() if v}
    return {
        "account_id": row.get("account_id"),
        "meta": meta,
        "secrets_masked": masked,
        "status": row.get("status"),
        "expires_at": row.get("expires_at"),
    }


@router.get("/platforms/{pid}/config")
async def platform_config(pid: str, user: dict = Me) -> dict:
    """Manage-view counterpart of /schema: masked credentials only, no field
    list. Used by the Manage modal to show what is currently stored."""
    existing = await _load_credential_for_ui(user["workspace_id"], pid)
    return {"existing": existing}


@router.post("/platforms/{pid}/test")
async def test_platform(pid: str, user: dict = Me) -> dict:
    """Best-effort liveness check — each adapter hits the provider with a
    cheap read-only call so the admin knows the credentials actually work
    before they leave the Channels page."""
    cfg = CONNECTORS.get(pid)
    if not cfg:
        raise HTTPException(404, "connector not found")
    provider = cfg["provider"]
    db = await get_db()
    cur = await db.execute(
        """
        SELECT secrets, meta, account_id FROM platform_credentials
         WHERE workspace_id = ? AND provider = ? AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (user["workspace_id"], provider),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "no credentials saved for this connector")
    rd = dict(row)
    secrets_blob = jl(rd.get("secrets"), {}) or {}
    meta = jl(rd.get("meta"), {}) or {}
    try:
        from ..channels.health_probes import probe
        result = await probe(provider, secrets=secrets_blob, meta=meta,
                             account_id=rd.get("account_id"))
    except Exception as e:
        log.exception("connector test failed for %s", pid)
        return {"ok": False, "error": repr(e)[:200]}
    return result


@router.post("/platforms/gmail/sync")
async def gmail_sync(user: dict = Me) -> dict:
    """Force a Gmail pull right now. The background poller in worker.py runs
    every 30 s so this is mostly a UX affordance — lets the user click
    "Sync inbox" and see inbound emails show up immediately instead of
    waiting for the next tick."""
    from ..channels.gmail_poller import poll_all_gmail_accounts
    result = await poll_all_gmail_accounts(workspace_id=user["workspace_id"])
    # Enrich with intent/health so the UI can tell "never connected" apart
    # from "connected but the stored token needs a reauth" — the two look
    # identical from the poller's perspective (both return accounts=0) but
    # mean very different things to an operator.
    overrides = await _workspace_overrides(user["workspace_id"])
    health = await _oauth_credential_health(user["workspace_id"])
    gmail_row = overrides.get("gmail") or {}
    result["connected"] = gmail_row.get("status") == "connected"
    result["needs_reauth"] = _needs_reauth_for(
        "gmail", gmail_row.get("status") or "available", health,
    )
    return result


@router.post("/platforms/outlook/sync")
async def outlook_sync(user: dict = Me) -> dict:
    """Force an Outlook pull right now. Mirrors the Gmail sync endpoint —
    background poller still runs every 30 s, this just lets the operator
    skip the wait."""
    from ..channels.outlook_poller import poll_all_outlook_accounts
    result = await poll_all_outlook_accounts(workspace_id=user["workspace_id"])
    overrides = await _workspace_overrides(user["workspace_id"])
    health = await _oauth_credential_health(user["workspace_id"])
    outlook_row = overrides.get("outlook") or {}
    result["connected"] = outlook_row.get("status") == "connected"
    result["needs_reauth"] = _needs_reauth_for(
        "outlook", outlook_row.get("status") or "available", health,
    )
    return result


# Connector ids that have a real `sync` implementation. Any other id falls
# through to the generic 422 below so the UI doesn't 404 silently when an
# operator clicks "Sync" on a connector that's webhook-driven (and so has
# nothing to "pull" on demand) or simply not yet implemented.
_SYNC_SUPPORTED = {"gmail", "outlook"}


@router.post("/platforms/{pid}/sync")
async def generic_sync(pid: str, user: dict = Me) -> dict:
    """Catch-all for sync requests on connectors without a per-id route.

    Returns 422 with a structured `{supported: false, reason}` response so
    the frontend can render a clear "Sync isn't applicable here" message
    instead of a generic 404 toast. Webhook-driven channels (WhatsApp,
    Telegram, Shopify) deliberately don't expose pull semantics."""
    if pid in _SYNC_SUPPORTED:
        # Should be unreachable — the per-id routes above match first — but
        # keep it explicit so a future router refactor can't slip through.
        raise HTTPException(500, "sync route shadowed; this is a bug")
    if pid not in _CATALOG_BY_ID:
        raise HTTPException(404, "platform not found")
    raise HTTPException(
        status_code=422,
        detail={
            "supported": False,
            "platform": pid,
            "reason": "sync not supported for this connector",
        },
    )
