"""Platforms (spec §3.8)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db, jl
from ..deps import Me

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


async def _workspace_overrides(workspace_id: str) -> dict[str, dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM platforms WHERE workspace_id = ?", (workspace_id,)
    )
    out: dict[str, dict] = {}
    for r in [dict(x) for x in await cur.fetchall()]:
        out[r["id"]] = r
    return out


@router.get("/platforms")
async def list_platforms(
    include: str | None = Query(None),
    user: dict = Me,
) -> list[dict]:
    overrides = await _workspace_overrides(user["workspace_id"])

    if include == "all":
        merged: list[dict] = []
        for entry in DEFAULT_CATALOG:
            ov = overrides.get(entry["id"])
            merged.append({
                **entry,
                "status": (ov or {}).get("status", "available"),
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
    return {"ok": True, "status": "disconnected"}
