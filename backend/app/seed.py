"""Seed a demo workspace, user, customers, orders, conversations, tickets, etc.

Run:  python -m app.seed           # wipes and re-seeds the demo workspace
Or let FastAPI's startup auto-run it when the DB is empty (see app/db.py).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from passlib.hash import argon2

from .db import get_db, close_db


def js(v): return json.dumps(v)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def wipe(db) -> None:
    # CASCADE means dropping workspaces/users unwinds everything downstream.
    # We still TRUNCATE the roots explicitly for clarity + seq reset on tickets.
    await db.execute(
        "TRUNCATE "
        "ticket_child, ticket_notes, ticket_timeline, tickets, "
        "conversation_notes, messages, conversations, "
        "orders, customers, "
        "teams_reactions, teams_messages, teams_dms, teams_channels, "
        "notifications, platforms, agents, "
        "auth_sessions, users, workspaces RESTART IDENTITY CASCADE"
    )
    # Tables that weren't in the original TRUNCATE list but may hold stale rows
    # from earlier seeds. Swallow missing-table errors so this works on older dbs.
    for table in ("kb_chunks", "sla_policies", "health_services", "incidents",
                  "incident_subscribers", "csat_responses", "sheets",
                  "ecommerce_events", "ecommerce_stores", "api_logs",
                  "apis", "integrations"):
        try:
            await db.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
        except Exception:
            pass


async def seed(db=None) -> None:
    """Wipe + re-seed. If `db` is None, acquire via `get_db()` (script path).
    When called from the startup auto-seed, the caller passes an already-built
    DB wrapper to avoid a re-entrant get_db() → _lock deadlock."""
    if db is None:
        db = await get_db()
    await wipe(db)

    now = datetime.now(timezone.utc)
    ws_id = str(uuid4())
    await db.execute(
        "INSERT INTO workspaces (id, name, slug) VALUES (?,?,?)",
        (ws_id, "The Sleep Company", "the-sleep-company"),
    )

    # Users
    users: list[tuple[str, str, str, str, str | None]] = [
        (str(uuid4()), "Aniket Singh", "demo@workspacex.com", "admin", argon2.hash("demo")),
        (str(uuid4()), "Priya Menon", "priya@workspacex.com", "agent", argon2.hash("demo")),
        (str(uuid4()), "Raj Kapoor", "raj@workspacex.com", "agent", argon2.hash("demo")),
        (str(uuid4()), "Divya Iyer", "divya@workspacex.com", "agent", argon2.hash("demo")),
    ]
    for uid, name, email, role, pw in users:
        await db.execute(
            """INSERT INTO users (id, workspace_id, name, email, password_hash, provider, role, status)
            VALUES (?,?,?,?,?, 'password', ?, 'online')""",
            (uid, ws_id, name, email, pw, role),
        )
    [me_uid, priya, raj, divya] = [u[0] for u in users]

    # Customers
    customers = [
        (str(uuid4()), "Arjun Verma", "+91-98100-12001", "arjun.verma@gmail.com",
         "A-104, Nirvana Courtyard, Sector 50, Gurugram 122018", 5, 1),
        (str(uuid4()), "Pooja Raman", "+91-98100-12002", "pooja.raman@outlook.com",
         "B-302, Hiranandani Estate, Thane 400607", 3, 0),
        (str(uuid4()), "Sanjay Gupta", "+91-98100-12003", "sanjay.gupta@yahoo.com",
         "Flat 12, Sunshine Tower, Andheri West, Mumbai 400053", 2, 0),
        (str(uuid4()), "Meera Iyer", "+91-98100-12004", "meera@company.in",
         "22, Defence Colony, New Delhi 110024", 7, 2),
        (str(uuid4()), "Rohit Shah", "+91-98100-12005", "rohit.shah@gmail.com",
         "C-8, Greenwoods Apartments, Kothrud, Pune 411038", 1, 0),
    ]
    for cid, name, phone, email, addr, orders, returns in customers:
        await db.execute(
            """INSERT INTO customers (id, workspace_id, name, phone, email, address, since, orders_count, returns_count)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (cid, ws_id, name, phone, email, addr,
             iso(now - timedelta(days=365 * 2)), orders, returns),
        )

    # Orders
    order_seeds = [
        (customers[0][0], "#SHO-20466915", "Delivered", "success",
         14999, [{"name": "Smart Grid Mattress", "sku": "SGM-KING", "qty": 1, "price": 14999}]),
        (customers[0][0], "#SHO-20466921", "Processing", "warning",
         7499,  [{"name": "Cooling Pillow", "sku": "CLP-02", "qty": 2, "price": 3749}]),
        (customers[1][0], "#SHO-20471002", "In Transit", "info",
         19999, [{"name": "Orthopedic Mattress", "sku": "ORM-QUEEN", "qty": 1, "price": 19999}]),
        (customers[3][0], "#SHO-20472100", "Delivered", "success",
         11499, [{"name": "Latex Pillow", "sku": "LX-01", "qty": 2, "price": 4499},
                 {"name": "Bed Sheet Set", "sku": "BS-QUEEN", "qty": 1, "price": 2501}]),
    ]
    for cid, oid, status, tone, total, items in order_seeds:
        await db.execute(
            """INSERT INTO orders (id, workspace_id, customer_id, source, placed_at, total, currency, status, status_tone, items)
            VALUES (?,?,?, 'shopify', ?, ?, 'INR', ?, ?, ?)""",
            (oid, ws_id, cid, iso(now - timedelta(days=8)), total, status, tone, js(items)),
        )

    # Conversations + messages
    conv_seeds = [
        # (customer_idx, channel, preview, messages=[(role, text, at_offset_minutes, from_name?)])
        (0, "WhatsApp", "Hi, my mattress hasn't arrived",
         [("customer", "Hi, my order #SHO-20466915 should have arrived by now.", -120, "Arjun Verma"),
          ("agent", "Hi Arjun — checking the tracker, please give me a minute.", -118, "Aniket Singh"),
          ("customer", "Sure, thanks.", -115, "Arjun Verma"),
          ("system", "Product Issue Report task started", -113, None)]),
        (1, "Mail", "Return request — Pillow is too firm",
         [("customer", "Please i wantt to return the pillow. its very firm.", -30, "Pooja Raman"),
          ("agent", "Hi Pooja — no worries, I can start the return. Is the packaging intact?", -28, "Aniket Singh")]),
        (2, "Instagram", "Is the smart grid mattress back in stock?",
         [("customer", "Hey! Is the queen smart grid mattress back in stock?", -600, "Sanjay Gupta")]),
        (3, "WhatsApp", "Address change for next order",
         [("customer", "I moved to 22, Defence Colony. Please update.", -4320, "Meera Iyer"),
          ("agent", "Updated, Meera — thanks!", -4318, "Aniket Singh")]),
        (4, "Live Chat", "Need a firm pillow for neck pain",
         [("customer", "My neck hurts — what pillow do you recommend for that?", -20, "Rohit Shah")]),
    ]
    conv_ids: list[str] = []
    for cust_idx, channel, preview, msgs in conv_seeds:
        conv_id = str(uuid4())
        conv_ids.append(conv_id)
        last = now + timedelta(minutes=msgs[-1][2]) if msgs else now
        await db.execute(
            """INSERT INTO conversations (id, workspace_id, customer_id, channel, preview,
                    last_message_at, last_inbound_at, assignee_id, unread_count)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (conv_id, ws_id, customers[cust_idx][0], channel, preview,
             iso(last), iso(last), me_uid if cust_idx % 2 == 0 else None,
             2 if cust_idx in (0, 4) else 0),
        )
        for role, text, offset, fname in msgs:
            await db.execute(
                """INSERT INTO messages (id, workspace_id, conversation_id, from_role,
                                         from_name, at, text, delivered_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid4()), ws_id, conv_id, role, fname,
                 iso(now + timedelta(minutes=offset)), text, iso(now + timedelta(minutes=offset))),
            )

    # Tickets — one per early conversation
    ticket_seeds = [
        (0, 0, "delay_delivery", "assigned",  me_uid, "Support",     "WhatsApp",
         "#SHO-20466915", "Delay in delivery",
         [("system", "Ticket created — intent: delay_delivery", -120),
          ("system", "Assigned to Aniket Singh", -119),
          ("system", "Customer opened WhatsApp conversation", -120)],
         {"escalation_reason": "SLA at risk", "replaced_item": False, "channels_tried": ["WhatsApp"]}),
        (1, 1, "return_request", "queued",    None,   "Returns",     "Mail",
         "#SHO-20471002", "Return requested — Pillow",
         [("system", "Ticket created — intent: return_request", -30)], {}),
        (2, 2, "product_enquiry", "queued",   None,   "Sales",       "Instagram",
         None, "Stock enquiry — Smart Grid queen",
         [("system", "Ticket created — intent: product_enquiry", -600)], {}),
        (3, 3, "address_change", "resolved",  me_uid, "Support",     "WhatsApp",
         None, "Address update",
         [("system", "Ticket created — intent: address_change", -4320),
          ("system", "Updated address on file", -4318),
          ("system", "Resolved", -4317)], {}),
        (0, None, "refund_pending", "blocked", priya,  "Finance",     "Call",
         "#SHO-20466921", "Refund pending — waiting on logistics",
         [("system", "Ticket created — intent: refund_pending", -2880),
          ("system", "Blocked: awaiting logistics confirmation", -2878)],
         {"refund_amount": 7499}),
    ]
    ticket_ids: list[int] = []
    for cust_idx, conv_idx, task, status, assignee, team, source, order_id, subject, tl, cf in ticket_seeds:
        created_at = iso(now - timedelta(minutes=abs(tl[0][2])))
        sla_due = iso(now + timedelta(days=1, hours=4))
        resolved_at = iso(now) if status == "resolved" else None
        cur = await db.execute(
            """INSERT INTO tickets (workspace_id, customer_id, conversation_id, order_id,
                    status, source, channel, task, team, assignee_id, subject, custom_fields,
                    sla_due_at, created_at, updated_at, resolved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (ws_id, customers[cust_idx][0],
             conv_ids[conv_idx] if conv_idx is not None else None,
             order_id, status, source, source, task, team, assignee, subject,
             js(cf), sla_due, created_at, created_at, resolved_at),
        )
        tid = cur.lastrowid
        ticket_ids.append(tid)
        for actor, text, offset in tl:
            await db.execute(
                "INSERT INTO ticket_timeline (ticket_id, actor, at, text) VALUES (?,?,?,?)",
                (tid, actor, iso(now + timedelta(minutes=offset)), text),
            )

    # A pending-child block for the refund ticket
    await db.execute(
        """INSERT INTO ticket_child (ticket_id, assignee_email, team, objective, prompt)
           VALUES (?,?,?,?,?)""",
        (ticket_ids[4], "logistics@slpc.com", "Logistics", "logistics_status",
         "Has the original pillow been picked up from the customer?"),
    )

    # Teams channels + dms + a few messages
    teams_channels = [
        (str(uuid4()), "#general", "Team-wide chat"),
        (str(uuid4()), "#support-escalations", "Urgent customer escalations"),
        (str(uuid4()), "#sales", "Sales coordination"),
    ]
    for cid, name, desc in teams_channels:
        await db.execute(
            "INSERT INTO teams_channels (id, workspace_id, name, description, privacy) VALUES (?,?,?,?, 'private')",
            (cid, ws_id, name, desc),
        )
    general = teams_channels[0][0]
    for txt, user in [
        ("Morning team!", me_uid),
        ("Anyone covering tickets from last night?", priya),
        ("On it — grabbing the refund queue.", raj),
    ]:
        await db.execute(
            "INSERT INTO teams_messages (id, workspace_id, channel_id, user_id, text, blocks) VALUES (?,?,?,?,?,?)",
            (str(uuid4()), ws_id, general, user, txt, js([{"type": "text", "value": txt}])),
        )

    dm_id = str(uuid4())
    await db.execute(
        "INSERT INTO teams_dms (id, workspace_id, member_ids) VALUES (?,?,?)",
        (dm_id, ws_id, js([me_uid, priya])),
    )

    # Seed a few catalog entries as "already connected" for the demo workspace.
    # Ids must match slugs in routers.platforms.DEFAULT_CATALOG so they merge
    # cleanly with the default catalog on /platforms?include=all.
    platforms = [
        ("whatsapp",  "messaging", "WhatsApp Business",  "Business API for chat support",    "connected", 132),
        ("gmail",     "email",     "Gmail",              "Gmail / Google Workspace mailbox", "connected", 87),
        ("instagram", "messaging", "Instagram DM",       "Instagram DM via Meta Graph",      "pending",   0),
        ("live-chat", "messaging", "Live Chat Widget",   "Embeddable site chat",             "connected", 41),
        ("telegram",  "messaging", "Telegram",           "Bot-based messaging",              "disconnected", 0),
        ("messenger", "messaging", "Facebook Messenger", "Facebook Messenger for Pages",     "disconnected", 0),
    ]
    for pid, kind, name, desc, status, count in platforms:
        await db.execute(
            """INSERT INTO platforms (id, workspace_id, kind, name, description, status, tickets_count)
            VALUES (?,?,?,?,?,?,?)""",
            (pid, ws_id, kind, name, desc, status, count),
        )

    # Notifications for the seeded user
    notifs = [
        ("assign",   "Ticket #1 assigned to you", "Delay in delivery — Arjun Verma", "/tickets?id=1"),
        ("reply",    "New reply on ticket #2",     "Pooja Raman replied",             "/tickets?id=2"),
        ("sla",      "SLA at risk on ticket #5",   "Refund pending — 30 min left",    "/tickets?id=5"),
        ("broadcast","Release 0.14 notes",          "New filters on the Tickets page", "/settings"),
    ]
    for t, title, body, link in notifs:
        await db.execute(
            "INSERT INTO notifications (user_id, type, title, body, link) VALUES (?,?,?,?,?)",
            (me_uid, t, title, body, link),
        )

    # SLA policies — priority overrides, otherwise 24h.
    for team, task, priority, hours in [
        (None, None, "urgent", 6),
        (None, None, "high", 12),
        ("Support", "delay_delivery", None, 18),
        ("Returns", "return_request", None, 48),
        ("Finance", "refund_pending", None, 36),
    ]:
        await db.execute(
            "INSERT INTO sla_policies (workspace_id, team, task, priority, hours) VALUES (?,?,?,?,?)",
            (ws_id, team, task, priority, hours),
        )

    # Health services — one row per integration we care about on the Health page.
    for name, category, status, up, lat, err, accent, letter in [
        ("WhatsApp Cloud API", "Messaging", "operational", 99.95, 180, 0.05, "#22c55e", "W"),
        ("Gmail API",          "Email",     "operational", 99.99, 220, 0.02, "#ef4444", "G"),
        ("Instagram Graph",    "Messaging", "degraded",    98.50, 420, 1.10, "#ec4899", "I"),
        ("Shopify Admin API",  "E-commerce","operational", 99.90, 160, 0.10, "#7c3aed", "S"),
        ("Postgres primary",   "Datastore", "operational", 100.00, 12, 0.00, "#0ea5e9", "P"),
        ("MinIO",              "Storage",   "operational", 99.98,  24, 0.00, "#f59e0b", "M"),
    ]:
        await db.execute(
            """INSERT INTO health_services (workspace_id, name, category, status, uptime_30d,
                                            latency_p95_ms, error_rate, last_check_at, accent, letter)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (workspace_id, name) DO NOTHING""",
            (ws_id, name, category, status, up, lat, err, iso(now), accent, letter),
        )

    # Incidents
    await db.execute(
        """INSERT INTO incidents (workspace_id, title, status, tone, components, summary, started_at)
           VALUES (?,?,?,?,?,?,?)""",
        (ws_id, "Instagram DM latency elevated", "monitoring", "warning",
         js(["Instagram Graph"]),
         "Meta Graph responses ~420ms p95 since 03:00 IST. Watching for recovery.",
         iso(now - timedelta(hours=5))),
    )

    # API Library sample + integrations
    integ_id = str(uuid4())
    await db.execute(
        """INSERT INTO integrations (id, workspace_id, name, description, kind, health)
           VALUES (?,?,?,?,?,?)""",
        (integ_id, ws_id, "Shopify Admin", "Orders + inventory lookups", "shopify", "good"),
    )
    for name, method, desc, spec in [
        ("shopify_order_lookup", "GET", "Look up an order by id",
         {"url": "https://{{shop}}.myshopify.com/admin/api/2024-04/orders/{{orderId}}.json"}),
        ("clickpost_tracking",   "GET", "Courier tracking via Clickpost",
         {"url": "https://api.clickpost.in/api/v1/track/?waybill={{awb}}"}),
    ]:
        await db.execute(
            """INSERT INTO apis (id, workspace_id, integration_id, name, method, description, spec)
               VALUES (?,?,?,?,?,?,?)""",
            (str(uuid4()), ws_id, integ_id, name, method, desc, js(spec)),
        )

    # E-commerce stores + recent events for the activity feed
    store_id = str(uuid4())
    await db.execute(
        """INSERT INTO ecommerce_stores (id, workspace_id, platform, name, status, orders_30d, health, last_sync_at, config)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (store_id, ws_id, "shopify", "the-sleep-co", "connected", 1464, 96, iso(now - timedelta(minutes=5)),
         js({"plan": "Shopify Plus", "letter": "S", "accent": "#7c3aed"})),
    )
    for typ, title, sub, tone, mins in [
        ("order",  "New order #SHO-20472244", "the-sleep-co", "success", 4),
        ("sync",   "Inventory sync complete", "142 SKUs updated", "info", 12),
        ("refund", "Refund issued #SHO-20466921", "₹7,499", "danger", 120),
        ("issue",  "Webhook retry (orders/create)", "last attempt in 30s", "warning", 240),
    ]:
        await db.execute(
            """INSERT INTO ecommerce_events (workspace_id, store_id, type, title, sub, tone, at)
               VALUES (?,?,?,?,?,?,?)""",
            (ws_id, store_id, typ, title, sub, tone, iso(now - timedelta(minutes=mins))),
        )

    # KB chunks so kb_search has something to return.
    for src, title, body in [
        ("macro:refunds", "Refund policy",
         "Refunds are processed within 5 business days of the return pickup. Cap is ₹10,000 for AI-initiated refunds; above that a human agent must approve."),
        ("macro:delivery", "Delivery timelines",
         "Standard delivery is 3-7 business days. Orders placed before 2pm IST dispatch same day."),
        ("macro:returns", "Return window",
         "Mattresses carry a 100-night trial; pillows must be unused and in packaging within 14 days."),
    ]:
        await db.execute(
            "INSERT INTO kb_chunks (workspace_id, source, title, body) VALUES (?,?,?,?)",
            (ws_id, src, title, body),
        )

    # Default orchestrator agent placeholder
    await db.execute(
        """INSERT INTO agents (id, workspace_id, name, model, persona, instructions, temperature, tools, status, active_version, last_published_at)
        VALUES (?,?,?,?,?,?,?,?, 'published', 1, ?)""",
        (str(uuid4()), ws_id, "default-orchestrator", "gpt-4.1",
         "Warm, concise customer support agent for The Sleep Company.",
         "Identify the customer, consult KB + orders, reply, open/update a ticket. Escalate above ₹10,000 refunds.",
         0.3, js([
             {"key": "find_customer",       "enabled": True},
             {"key": "list_customer_orders","enabled": True},
             {"key": "get_shipping_status", "enabled": True},
             {"key": "kb_search",           "enabled": True},
             {"key": "create_ticket",       "enabled": True},
             {"key": "send_reply",          "enabled": True},
             {"key": "escalate_to_human",   "enabled": True},
         ]),
         iso(now - timedelta(days=1))),
    )

    await db.commit()
    print(f"Seeded workspace {ws_id}")
    print("Demo login: demo@workspacex.com / demo")


async def main() -> None:
    try:
        await seed()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
