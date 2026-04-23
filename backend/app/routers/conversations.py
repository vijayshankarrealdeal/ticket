"""Conversations + messages (spec §3.3).

Frontend shape (see src/pages/Conversations.jsx):
  Conversation {
    id, customerId, channel, date, preview, phone?, email?,
    assignee?, unread, orderId?, customer, notes[], recordings[],
    attachments[], tickets[], messages[]
  }
  Message { from: 'me'|'them'|'system', at, text, delivered?, fromName?, attachments?, replyTo? }
"""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query

from ..db import get_db, jl, js
from ..deps import AgentOrAdmin, Me
from ..idempotency import check_idempotency, save_idempotency
from ..schemas import MessageIn, NoteIn
from ..ws import broker
from .notifications import create_notification

log = logging.getLogger(__name__)
router = APIRouter(tags=["conversations"])


# Rate-limit cache for the opportunistic Gmail pull kicked from
# `GET /conversations`. Keyed by workspace_id → last-poll timestamp so a
# page refresh doesn't hammer the Gmail API.
_LAST_GMAIL_POLL: dict[str, datetime] = {}


def _maybe_kick_gmail_poll(workspace_id: str) -> None:
    """Fire-and-forget Gmail poll, at most once per workspace per 20 s. Lets
    the inbox refresh itself on page load without requiring the background
    worker to be running (handy during localhost dev)."""
    now = datetime.now(timezone.utc)
    last = _LAST_GMAIL_POLL.get(workspace_id)
    if last and (now - last).total_seconds() < 20:
        return
    _LAST_GMAIL_POLL[workspace_id] = now
    async def _go() -> None:
        try:
            from ..channels.gmail_poller import poll_all_gmail_accounts
            await poll_all_gmail_accounts(workspace_id=workspace_id)
        except Exception:
            log.exception("opportunistic gmail poll failed (ws=%s)", workspace_id)
    asyncio.create_task(_go())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        # Store is ISO or SQLite 'YYYY-MM-DD HH:MM:SS'
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return dt.strftime("%I:%M %p").lstrip("0")


async def _assignee_name(db, uid: str | None) -> str | None:
    if not uid:
        return None
    cur = await db.execute("SELECT name FROM users WHERE id = ?", (uid,))
    r = await cur.fetchone()
    return r[0] if r else None


async def _serialize_conversation(db, row: dict, include_detail: bool = False) -> dict:
    cur = await db.execute("SELECT * FROM customers WHERE id = ?", (row["customer_id"],))
    cust = await cur.fetchone()
    customer = dict(cust) if cust else {}
    assignee = await _assignee_name(db, row.get("assignee_id"))

    out = {
        "id": row["id"],
        "customerId": row["customer_id"],
        "channel": row["channel"],
        "date": _fmt_time(row.get("last_message_at")) or _fmt_time(row.get("created_at")),
        "preview": row.get("preview") or "",
        "phone": customer.get("phone"),
        "email": customer.get("email"),
        "isPhone": bool(customer.get("phone")),
        "assignee": assignee,
        "status": row.get("status") or "open",
        "unread": row.get("unread_count") or 0,
        "orderId": row.get("order_id"),
        "name": customer.get("name"),
    }
    if include_detail:
        cur = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY at",
            (row["id"],),
        )
        msg_rows = [dict(r) for r in await cur.fetchall()]
        messages = [_serialize_message(m) for m in msg_rows]

        cur = await db.execute(
            "SELECT * FROM conversation_notes WHERE conversation_id = ? ORDER BY at",
            (row["id"],),
        )
        notes = [
            {"id": n["id"], "author": n["author_name"], "at": _fmt_time(n["at"]), "text": n["text"]}
            for n in [dict(r) for r in await cur.fetchall()]
        ]

        cur = await db.execute(
            "SELECT * FROM tickets WHERE conversation_id = ? ORDER BY created_at DESC",
            (row["id"],),
        )
        tickets = []
        for t in [dict(r) for r in await cur.fetchall()]:
            tickets.append({
                "id": f"#{t['id']}",
                "task": t["task"],
                "status": t["status"],
                "assignee": await _assignee_name(db, t.get("assignee_id")),
                "sla": _sla_string(t.get("sla_due_at")),
            })

        recent_orders = []
        if customer.get("id"):
            cur = await db.execute(
                "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT 5",
                (customer["id"],),
            )
            recent_orders = [_serialize_order(dict(r)) for r in await cur.fetchall()]

        out.update({
            "messages": messages,
            "notes": notes,
            "tickets": tickets,
            "recordings": [],
            "attachments": [a for m in msg_rows for a in jl(m.get("attachments"), [])],
            "customer": {
                "name": customer.get("name"),
                "phone": customer.get("phone"),
                "email": customer.get("email"),
                "since": customer.get("since"),
                "address": customer.get("address"),
                "orders": customer.get("orders_count", 0),
                "returns": customer.get("returns_count", 0),
                "otherAddresses": jl(customer.get("other_addresses"), []),
                "recentOrders": recent_orders,
            },
        })
    return out


def _serialize_message(m: dict) -> dict:
    reply = None
    raw_reply = m.get("reply_to_id")
    # reply_to snapshot stored by us in attachments/raw? For simplicity, show nothing.
    return {
        "id": m["id"],
        "from": m["from_role"] if m["from_role"] in ("me", "them", "system") else _map_from(m["from_role"]),
        "fromName": m.get("from_name"),
        "at": _fmt_time(m["at"]),
        "text": m.get("text") or "",
        "delivered": bool(m.get("delivered_at")),
        "attachments": jl(m.get("attachments"), []),
        "replyTo": reply,
    }


def _map_from(role: str) -> str:
    # UI uses me/them/system where 'me' = the agent, 'them' = the customer.
    if role in ("agent", "ai"):
        return "me"
    if role == "customer":
        return "them"
    return "system"


def _sla_string(due: str | None) -> str | None:
    if not due:
        return None
    try:
        dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = dt - datetime.now(timezone.utc)
    if delta.total_seconds() <= 0:
        return "overdue"
    hours = int(delta.total_seconds() // 3600)
    d, h = divmod(hours, 24)
    return f"{d}d {h}h" if d else f"{h}h"


def _serialize_order(o: dict) -> dict:
    return {
        "id": o["id"],
        "time": _fmt_time(o.get("placed_at")),
        "status": o.get("status"),
        "statusTone": o.get("status_tone") or "info",
        "total": o.get("total"),
        "itemsCount": sum(i.get("qty", 1) for i in jl(o.get("items"), [])),
        "items": jl(o.get("items"), []),
    }


@router.get("/conversations")
async def list_conversations(
    channel: str | None = Query(None),
    tab: str | None = Query(None),
    q: str | None = Query(None),
    user: dict = Me,
) -> list[dict]:
    # Fire-and-forget Gmail pull so inbound email shows up even if the
    # background worker isn't running (localhost dev typically runs only
    # uvicorn). Rate-limited to one poll per workspace per 20 s via the
    # module-level `_LAST_GMAIL_POLL` cache so refreshing the page doesn't
    # hammer the Gmail API.
    _maybe_kick_gmail_poll(user["workspace_id"])

    db = await get_db()
    clauses = ["c.workspace_id = ?"]
    params: list = [user["workspace_id"]]
    if channel and channel.lower() not in ("all", ""):
        clauses.append("lower(c.channel) = ?")
        params.append(channel.lower())
    if tab == "unassigned":
        clauses.append("c.assignee_id IS NULL")
    elif tab == "pending":
        clauses.append("c.status = 'pending'")
    if q:
        clauses.append("(c.preview ILIKE ? OR cu.name ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    sql = f"""
      SELECT c.*
      FROM conversations c
      LEFT JOIN customers cu ON cu.id = c.customer_id
      WHERE {' AND '.join(clauses)}
      ORDER BY c.last_message_at DESC, c.created_at DESC
      LIMIT 200
    """
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    return [await _serialize_conversation(db, r) for r in rows]


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "conversation not found")
    return await _serialize_conversation(db, dict(row), include_detail=True)


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: str, user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "conversation not found")
    cur = await db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY at", (conv_id,)
    )
    return [_serialize_message(dict(r)) for r in await cur.fetchall()]


@router.post("/conversations/{conv_id}/messages")
async def post_message(
    conv_id: str,
    body: MessageIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = AgentOrAdmin,
) -> dict:
    # Fall back to body.tempId as the idempotency key so the optimistic-UI
    # contract (wire.md §2) is protected from double-submits out of the box.
    idem_key = idempotency_key or (body.tempId if body.tempId else None)
    scope = f"conv.{conv_id}.msg"
    cached = await check_idempotency(user, scope, idem_key)
    if cached is not None:
        return cached
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    conv = await cur.fetchone()
    if not conv:
        raise HTTPException(404, "conversation not found")

    msg_id = str(uuid4())
    now = _now()
    preview = (body.text or "")[:160]
    await db.execute(
        """
        INSERT INTO messages (id, workspace_id, conversation_id, from_role, from_user_id,
                              from_name, at, text, attachments, reply_to_id, delivered_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (msg_id, user["workspace_id"], conv_id, "agent", user["id"], user["name"],
         now, body.text, js(body.attachments), (body.replyTo or {}).get("id"), now),
    )
    await db.execute(
        "UPDATE conversations SET preview = ?, last_message_at = ? WHERE id = ?",
        (preview, now, conv_id),
    )
    await db.commit()

    msg = {
        "id": msg_id,
        "tempId": body.tempId,
        "from": "me",
        "fromName": user["name"],
        "at": _fmt_time(now),
        "text": body.text or "",
        "delivered": True,
        "attachments": body.attachments,
        "replyTo": body.replyTo,
    }
    await broker.publish(f"conv:{conv_id}", "message.created", msg)
    await broker.publish(
        f"inbox:{user['workspace_id']}",
        "conversation.updated",
        {"id": conv_id, "preview": preview, "date": _fmt_time(now)},
    )
    # Append timeline for linked tickets
    cur = await db.execute("SELECT id FROM tickets WHERE conversation_id = ?", (conv_id,))
    for (tid,) in await cur.fetchall():
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, actor_user_id, text) VALUES (?,?,?,?)",
            (tid, user["email"], user["id"], f"Agent {user['name']} replied on {conv['channel']}"),
        )
        await db.commit()
        await broker.publish(f"ticket:{tid}", "ticket.timeline.appended", {"ticket_id": tid})
    await save_idempotency(user, scope, idem_key, msg)
    return msg


@router.post("/conversations/{conv_id}/notes")
async def post_note(conv_id: str, body: NoteIn, user: dict = AgentOrAdmin) -> dict:
    db = await get_db()
    note_id = str(uuid4())
    now = _now()
    await db.execute(
        """
        INSERT INTO conversation_notes (id, conversation_id, author_id, author_name, at, text)
        VALUES (?,?,?,?,?,?)
        """,
        (note_id, conv_id, user["id"], user["name"], now, body.text),
    )
    await db.commit()
    return {"id": note_id, "author": user["name"], "at": _fmt_time(now), "text": body.text}


@router.post("/conversations/{conv_id}/typing")
async def typing(conv_id: str, body: dict, user: dict = AgentOrAdmin) -> dict:
    await broker.publish(
        f"conv:{conv_id}",
        "typing.started" if body.get("on", True) else "typing.stopped",
        {"user_id": user["id"], "name": user["name"]},
    )
    return {"ok": True}


@router.post("/messages/{message_id}/read")
async def mark_read(message_id: str, user: dict = Me) -> dict:
    db = await get_db()
    now = _now()
    await db.execute("UPDATE messages SET read_at = ? WHERE id = ?", (now, message_id))
    await db.commit()
    return {"ok": True}


async def resolve_or_create_customer(
    *,
    workspace_id: str,
    phone: str | None = None,
    email: str | None = None,
    wa_id: str | None = None,
    psid: str | None = None,
    shopify_id: str | None = None,
    name: str | None = None,
) -> str:
    """Find-or-create a customer row by whichever channel identifier we have.
    Webhook routers use this as a thin idempotency layer around inbound
    messages — repeated hits from the same sender don't spawn duplicates."""
    db = await get_db()
    clauses = []
    params: list = []
    if phone:
        clauses.append("phone = ?"); params.append(phone)
    if email:
        clauses.append("email = ?"); params.append(email.lower())
    if wa_id:
        clauses.append("wa_id = ?"); params.append(wa_id)
    if psid:
        clauses.append("psid = ?"); params.append(psid)
    if shopify_id:
        clauses.append("shopify_id = ?"); params.append(shopify_id)
    if clauses:
        sql = (
            f"SELECT id FROM customers WHERE workspace_id = ? AND ({' OR '.join(clauses)}) LIMIT 1"
        )
        cur = await db.execute(sql, (workspace_id, *params))
        row = await cur.fetchone()
        if row:
            return row[0]
    cid = str(uuid4())
    await db.execute(
        """
        INSERT INTO customers (id, workspace_id, name, phone, email, wa_id, psid,
                               shopify_id, kind, since)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'customer', CURRENT_DATE)
        """,
        (cid, workspace_id, name or email or phone or wa_id or psid or "Customer",
         phone, (email or "").lower() or None, wa_id, psid, shopify_id),
    )
    await db.commit()
    return cid


async def resolve_or_create_conversation(
    *,
    workspace_id: str,
    customer_id: str,
    channel: str,
    external_thread_id: str | None = None,
) -> str:
    """Find-or-create a conversation row for `(workspace, customer, channel)`.
    If `external_thread_id` is provided (Gmail thread id, Outlook conversation
    id), we match on that first so distinct threads with the same customer
    stay separate."""
    db = await get_db()
    if external_thread_id:
        cur = await db.execute(
            """
            SELECT id FROM conversations
             WHERE workspace_id = ? AND channel = ? AND external_thread_id = ?
             LIMIT 1
            """,
            (workspace_id, channel, external_thread_id),
        )
        row = await cur.fetchone()
        if row:
            return row[0]
    cur = await db.execute(
        """
        SELECT id FROM conversations
         WHERE workspace_id = ? AND customer_id = ? AND channel = ?
           AND status NOT IN ('closed')
         ORDER BY last_message_at DESC LIMIT 1
        """,
        (workspace_id, customer_id, channel),
    )
    row = await cur.fetchone()
    if row:
        return row[0]
    cid = str(uuid4())
    await db.execute(
        """
        INSERT INTO conversations (id, workspace_id, customer_id, channel,
                                   external_thread_id, status)
        VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (cid, workspace_id, customer_id, channel, external_thread_id),
    )
    await db.commit()
    return cid


async def ingest_inbound_message(
    *,
    workspace_id: str,
    conversation_id: str,
    text: str,
    attachments: list | None = None,
    from_name: str = "Customer",
    external_message_id: str | None = None,
    auto_reply: bool = True,
    actor_user_id: str | None = None,
) -> dict:
    """Shared inbound pipeline — persists a customer message, runs the intent
    classifier, executes its `attach | reopen | create` decision, fans out the
    WS events, and (optionally) kicks off the AI auto-reply. Used by
    `POST /conversations/:id/inbound` **and** every channel webhook (WhatsApp /
    Gmail / Outlook / Instagram / Shopify / Slack / Voice) so the auth'd
    simulator and the real channel adapters share one code path.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND workspace_id = ?",
        (conversation_id, workspace_id),
    )
    conv_row = await cur.fetchone()
    if not conv_row:
        raise HTTPException(404, "conversation not found")
    conv = dict(conv_row)

    attachments = attachments or []
    text = text or ""
    preview = text[:160]
    msg_id = str(uuid4())
    now = _now()
    await db.execute(
        """
        INSERT INTO messages (id, workspace_id, conversation_id, from_role, from_name,
                              external_message_id, at, text, attachments)
        VALUES (?,?,?, 'customer', ?, ?, ?, ?, ?)
        """,
        (msg_id, workspace_id, conversation_id, from_name,
         external_message_id, now, text, js(attachments)),
    )
    await db.execute(
        """
        UPDATE conversations
           SET preview = ?, last_message_at = ?, last_inbound_at = ?,
               unread_count = unread_count + 1
         WHERE id = ?
        """,
        (preview, now, now, conversation_id),
    )
    await db.commit()

    # Stage-1 intent classification (tickets.intent_classify can also call
    # Stage-2 LLM when OPENAI_API_KEY is set and Stage-1 confidence is low).
    from .tickets import intent_classify
    classifier_user = {"workspace_id": workspace_id, "id": actor_user_id}
    decision = await intent_classify(
        {"conversationId": conversation_id, "text": text}, classifier_user,
    )
    ticket_id = decision.get("ticketId")
    # AI leads resolution by default. A human is only looped in when intent
    # confidence is below `AI_CONFIDENCE_THRESHOLD` (classifier doesn't know
    # what the customer is asking for) or when auto_reply is explicitly off.
    # At runtime the AI can still fall back to human via `escalate_to_human`
    # (→ status='blocked') — that's the other human-touch path.
    AI_CONFIDENCE_THRESHOLD = 0.5
    confidence = float(decision.get("confidence") or 0.0)
    ai_can_handle = auto_reply and confidence >= AI_CONFIDENCE_THRESHOLD
    new_status = "in_progress" if ai_can_handle else "queued"
    if decision["decision"] == "reopen" and ticket_id:
        await db.execute(
            "UPDATE tickets SET status = ?, resolved_at = NULL, updated_at = now() WHERE id = ?",
            (new_status, ticket_id),
        )
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'customer', ?)",
            (ticket_id, f"Reopened — customer replied: {preview}"),
        )
        await db.commit()
    elif decision["decision"] == "attach" and ticket_id:
        # Customer replied on a live ticket — if it was parked waiting on them,
        # flip back to in_progress so the AI (or assignee) picks it up again.
        await db.execute(
            """
            UPDATE tickets SET status = ?, updated_at = now()
             WHERE id = ? AND status IN ('waiting_for_customer','queued')
            """,
            (new_status, ticket_id),
        )
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'customer', ?)",
            (ticket_id, f"Customer replied: {preview}"),
        )
        await db.commit()
    elif decision["decision"] == "create":
        from datetime import timedelta
        sla_due = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        cur = await db.execute(
            """
            INSERT INTO tickets (workspace_id, customer_id, conversation_id, source, channel,
                                 task, team, priority, subject, description,
                                 tags, custom_fields, status, sla_due_at,
                                 intent_key, intent_confidence, order_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?,?,?)
            RETURNING id
            """,
            (workspace_id, conv["customer_id"], conversation_id,
             conv["channel"], conv["channel"],
             decision["intentKey"], "Support", "normal",
             preview[:80], text, js([]), js({}), new_status, sla_due,
             decision["intentKey"], decision["confidence"], decision.get("orderRef")),
        )
        ticket_id = cur.lastrowid
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
            (ticket_id, f"Ticket auto-created by intent classifier — {decision['reason']}"),
        )
        await db.commit()
        await broker.publish(
            f"inbox:{workspace_id}", "ticket.created",
            {"id": ticket_id, "subject": preview[:80], "task": decision["intentKey"]},
        )
        # Notify every agent/admin in the workspace that a new ticket landed.
        # New tickets are unassigned (status='queued'), so the assignee-only
        # notify below would never fire — agents would see nothing in the bell.
        cur = await db.execute(
            "SELECT id FROM users WHERE workspace_id = ? AND role IN ('admin','agent')",
            (workspace_id,),
        )
        recipients = [r[0] for r in await cur.fetchall()]
        for uid in recipients:
            if uid == actor_user_id:
                continue
            await create_notification(
                uid, "ticket",
                title=f"New ticket #{ticket_id} from {from_name}",
                body=preview,
                link=f"/tickets?id={ticket_id}",
            )

    msg = {
        "id": msg_id,
        "from": "them",
        "fromName": from_name,
        "at": _fmt_time(now),
        "text": text,
        "delivered": True,
        "attachments": attachments,
    }
    await broker.publish(f"conv:{conversation_id}", "message.created", msg)
    await broker.publish(
        f"inbox:{workspace_id}", "conversation.updated",
        {"id": conversation_id, "preview": preview, "date": _fmt_time(now)},
    )
    if ticket_id:
        await broker.publish(
            f"ticket:{ticket_id}", "ticket.timeline.appended", {"ticket_id": ticket_id}
        )
        cur = await db.execute(
            "SELECT assignee_id FROM tickets WHERE id = ?", (ticket_id,)
        )
        trow = await cur.fetchone()
        if trow and trow["assignee_id"] and trow["assignee_id"] != actor_user_id:
            await create_notification(
                trow["assignee_id"], "reply",
                title=f"{from_name} replied on ticket #{ticket_id}",
                body=preview,
                link=f"/conversations?id={conversation_id}",
            )
        await db.execute(
            "UPDATE tickets SET last_customer_at = ? WHERE id = ?",
            (now, ticket_id),
        )
        await db.commit()

    if ai_can_handle:
        asyncio.create_task(_run_inbound_ai_reply(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            channel=conv.get("channel"),
            customer_id=conv.get("customer_id"),
            actor_user_id=actor_user_id,
            prompt=text,
        ))
    elif auto_reply and ticket_id:
        # Low-confidence intent: hand off to a human instead of drafting a
        # reply we can't ground. Leave a trace so agents see why the AI stepped
        # back.
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
            (ticket_id,
             f"AI held back — intent confidence {confidence:.2f} below {AI_CONFIDENCE_THRESHOLD:.2f}; routed to human"),
        )
        await db.commit()

    return {"message": msg, "intent": decision, "ticketId": ticket_id}


@router.post("/conversations/{conv_id}/inbound")
async def post_inbound(
    conv_id: str,
    body: dict,
    user: dict = AgentOrAdmin,
) -> dict:
    """Simulates a channel adapter receiving a customer message. Wraps the
    shared `ingest_inbound_message` pipeline so the auth'd simulator and the
    real channel webhooks (routers/webhooks.py) agree on behaviour.

    Body: {text, attachments?, fromName?, externalMessageId?, autoReply?}
    """
    return await ingest_inbound_message(
        workspace_id=user["workspace_id"],
        conversation_id=conv_id,
        text=body.get("text") or "",
        attachments=body.get("attachments") or [],
        from_name=body.get("fromName") or "Customer",
        external_message_id=body.get("externalMessageId"),
        auto_reply=bool(body.get("autoReply", True)),
        actor_user_id=user["id"],
    )


async def _run_inbound_ai_reply(*, workspace_id: str, conversation_id: str,
                                channel: str | None, customer_id: str | None,
                                actor_user_id: str | None, prompt: str) -> None:
    """Background helper: select the workspace's preferred agent, then stream
    its reply into the live conversation. Swallows exceptions so a broken
    agent never poisons the inbound POST response."""
    from ..ai.orchestrator import AgentCtx, select_inbound_agent, stream_inbound_reply
    import uuid as _uuid

    try:
        agent = await select_inbound_agent(workspace_id)
        ctx = AgentCtx(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            channel=channel,
            actor_user_id=actor_user_id,
            trace_id=f"trc_{_uuid.uuid4().hex[:12]}",
            agent_id=agent.get("id"),
            agent_name=agent.get("name"),
        )
        await stream_inbound_reply(ctx, prompt, model=agent.get("model") or "gpt-4.1-mini")
    except Exception:
        log.exception("inbound AI reply failed (conv=%s)", conversation_id)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: dict = AgentOrAdmin) -> dict:
    """Remove a conversation, its tickets (walking both parent and child links),
    and the underlying thread at the source channel (Gmail trash, Outlook
    delete — WhatsApp/IG/Slack have no per-thread delete). Source deletion is
    best-effort: a revoked token or offline provider must not strand the local
    rows. Messages, notes, timeline and ticket_child entries are removed by
    the schema's ON DELETE CASCADE."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    conv = await cur.fetchone()
    if not conv:
        raise HTTPException(404, "conversation not found")
    conv = dict(conv)

    # Gather all tickets reachable from this conversation along parent_ticket_id
    # and child (inverse) edges. Both directions are fused into a single
    # recursive term (OR-join) — Postgres only permits one recursive branch per
    # WITH RECURSIVE, and splitting parent/child walks across two UNIONs makes
    # the middle branch an illegal forward self-reference (InvalidRecursionError).
    cur = await db.execute(
        """
        WITH RECURSIVE related AS (
            SELECT id, parent_ticket_id
              FROM tickets
             WHERE conversation_id = ? AND workspace_id = ?
            UNION
            SELECT t.id, t.parent_ticket_id
              FROM tickets t JOIN related r
                ON t.id = r.parent_ticket_id OR t.parent_ticket_id = r.id
             WHERE t.workspace_id = ?
        )
        SELECT DISTINCT id FROM related
        """,
        (conv_id, user["workspace_id"], user["workspace_id"]),
    )
    ticket_ids = [r[0] for r in await cur.fetchall()]

    # Collect the external message ids so per-channel deletion (Outlook) can
    # target individual messages — Gmail uses the thread id directly.
    cur = await db.execute(
        "SELECT external_message_id FROM messages WHERE conversation_id = ? AND external_message_id IS NOT NULL",
        (conv_id,),
    )
    ext_msg_ids = [r[0] for r in await cur.fetchall()]

    source_result = None
    try:
        from ..channels.source_delete import delete_conversation_at_source
        source_result = await delete_conversation_at_source(
            workspace_id=user["workspace_id"],
            channel=conv.get("channel"),
            external_thread_id=conv.get("external_thread_id"),
            external_message_ids=ext_msg_ids,
        )
    except Exception:
        log.exception("source-side delete failed (conv=%s)", conv_id)

    # Break any parent_ticket_id links from tickets outside our set so the
    # FK doesn't block the delete when a parent lives elsewhere (shouldn't
    # happen since the CTE walks up, but be defensive).
    if ticket_ids:
        placeholders = ",".join(["?"] * len(ticket_ids))
        await db.execute(
            f"UPDATE tickets SET parent_ticket_id = NULL WHERE parent_ticket_id IN ({placeholders})",
            tuple(ticket_ids),
        )
        await db.execute(
            f"DELETE FROM tickets WHERE id IN ({placeholders})",
            tuple(ticket_ids),
        )
        await db.commit()

    # Conversation delete cascades to messages + conversation_notes via schema FKs.
    await db.execute(
        "DELETE FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    await db.commit()

    await broker.publish(
        f"inbox:{user['workspace_id']}", "conversation.deleted", {"id": conv_id},
    )
    await broker.publish(f"conv:{conv_id}", "conversation.deleted", {"id": conv_id})
    for tid in ticket_ids:
        await broker.publish(f"ticket:{tid}", "ticket.deleted", {"id": tid})

    return {
        "ok": True,
        "conversationId": conv_id,
        "deletedTicketIds": ticket_ids,
        "source": source_result,
    }


@router.get("/conversations/{conv_id}/tickets")
async def conversation_tickets(conv_id: str, user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE conversation_id = ? AND workspace_id = ? ORDER BY created_at DESC",
        (conv_id, user["workspace_id"]),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for t in rows:
        out.append({
            "id": f"#{t['id']}",
            "task": t["task"],
            "status": t["status"],
            "source": t["source"],
            "channel": t["channel"],
            "assignee": await _assignee_name(db, t.get("assignee_id")),
            "sla": _sla_string(t.get("sla_due_at")),
        })
    return out
