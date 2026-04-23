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

from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db, jl, js
from ..deps import Me
from ..schemas import MessageIn, NoteIn
from ..ws import broker

router = APIRouter(tags=["conversations"])


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
async def post_message(conv_id: str, body: MessageIn, user: dict = Me) -> dict:
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
    return msg


@router.post("/conversations/{conv_id}/notes")
async def post_note(conv_id: str, body: NoteIn, user: dict = Me) -> dict:
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
async def typing(conv_id: str, body: dict, user: dict = Me) -> dict:
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
