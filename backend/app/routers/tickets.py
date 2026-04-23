"""Tickets (spec §3.4)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db, jl, js
from ..deps import Me
from ..schemas import (
    ChildTicketIn,
    TicketCreateIn,
    TicketPatchIn,
    TicketResolveIn,
)
from ..ws import broker

router = APIRouter(tags=["tickets"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return dt.strftime("%I:%M %p").lstrip("0")


def _ago(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _sla_string(due: str | None) -> str:
    if not due:
        return ""
    try:
        dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - datetime.now(timezone.utc)
    if delta.total_seconds() <= 0:
        return "overdue"
    hours = int(delta.total_seconds() // 3600)
    d, h = divmod(hours, 24)
    return f"{d}d {h}h" if d else f"{h}h"


STATUS_TONE = {
    "assigned": "info",
    "queued": "warning",
    "blocked": "danger",
    "resolved": "success",
    "closed": "muted",
    "in_progress": "info",
    "waiting_for_customer": "warning",
}


async def _get_user_name(db, uid: str | None) -> str | None:
    if not uid:
        return None
    cur = await db.execute("SELECT name FROM users WHERE id = ?", (uid,))
    r = await cur.fetchone()
    return r[0] if r else None


async def _serialize_ticket(db, t: dict, detail: bool = False) -> dict:
    cur = await db.execute("SELECT * FROM customers WHERE id = ?", (t["customer_id"],))
    cust = await cur.fetchone()
    customer = dict(cust) if cust else {}
    assignee_name = await _get_user_name(db, t.get("assignee_id"))
    base = {
        "id": f"#{t['id']}",
        "idNumber": t["id"],
        "ago": _ago(t.get("created_at")),
        "customer": customer.get("name"),
        "customerId": customer.get("id"),
        "channel": t["channel"],
        "source": t["source"],
        "task": t["task"],
        "team": t["team"],
        "status": t["status"],
        "statusClass": STATUS_TONE.get(t["status"], "info"),
        "assignee": assignee_name,
        "sla": _sla_string(t.get("sla_due_at")),
        "priority": t.get("priority"),
        "tags": jl(t.get("tags"), []),
        "subject": t.get("subject"),
        "description": t.get("description"),
        "orderStatus": t.get("order_status"),
        "orderPlacedOn": t.get("order_placed_on"),
        "createdAt": t.get("created_at"),
    }
    if not detail:
        return base

    cur = await db.execute(
        "SELECT * FROM ticket_timeline WHERE ticket_id = ? ORDER BY at", (t["id"],)
    )
    timeline = [
        {"at": _fmt_time(r["at"]), "text": r["text"], "actor": r["actor"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]

    cur = await db.execute(
        "SELECT * FROM ticket_notes WHERE ticket_id = ? ORDER BY at", (t["id"],)
    )
    notes = [
        {"author": r["author"], "at": _fmt_time(r["at"]), "text": r["text"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]

    order = None
    if t.get("order_id"):
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (t["order_id"],))
        o = await cur.fetchone()
        if o:
            o = dict(o)
            order = {
                "id": o["id"],
                "price": o["total"],
                "placedOn": o.get("placed_at"),
                "items": jl(o.get("items"), []),
            }

    child = None
    cur = await db.execute("SELECT * FROM ticket_child WHERE ticket_id = ?", (t["id"],))
    child_row = await cur.fetchone()
    if child_row:
        c = dict(child_row)
        child = {
            "assigneeEmail": c.get("assignee_email"),
            "team": c.get("team"),
            "objective": c.get("objective"),
            "prompt": c.get("prompt"),
            "resolution": c.get("resolution"),
        }

    base.update({
        "customerDetails": {
            "phone": customer.get("phone"),
            "email": customer.get("email"),
            "since": customer.get("since"),
            "address": customer.get("address"),
            "orders": customer.get("orders_count", 0),
            "returns": customer.get("returns_count", 0),
        },
        "timeline": timeline,
        "notes": notes,
        "order": order,
        "customFields": jl(t.get("custom_fields"), {}),
        "pendingChild": child,
        "voc": t.get("voc"),
        "resolutionNotes": t.get("resolution_notes"),
    })
    return base


def _parse_ticket_id(id_str: str) -> int:
    s = id_str.lstrip("#")
    try:
        return int(s)
    except ValueError:
        raise HTTPException(400, f"invalid ticket id: {id_str}")


@router.get("/tickets")
async def list_tickets(
    filter: str | None = Query(None, alias="filter"),
    sort: str | None = Query(None),
    q: str | None = Query(None),
    shareable: int | None = Query(None),
    user: dict = Me,
) -> list[dict]:
    db = await get_db()
    clauses = ["t.workspace_id = ?"]
    params: list = [user["workspace_id"]]
    if filter == "my-tickets":
        clauses.append("t.assignee_id = ?")
        params.append(user["id"])
    elif filter == "unassigned":
        clauses.append("t.assignee_id IS NULL")
    elif filter == "blocked":
        clauses.append("t.status = 'blocked'")
    elif filter == "all-open" or not filter:
        clauses.append("t.status NOT IN ('resolved','closed')")
    if q:
        clauses.append("(t.task ILIKE ? OR t.subject ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    order = "t.created_at DESC"
    if sort == "oldest":
        order = "t.created_at ASC"
    elif sort == "sla-asc":
        order = "t.sla_due_at ASC"
    elif sort == "sla-desc":
        order = "t.sla_due_at DESC"

    sql = f"SELECT t.* FROM tickets t WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT 500"
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    return [await _serialize_ticket(db, r) for r in rows]


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, user: dict = Me) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE id = ? AND workspace_id = ?",
        (tid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "ticket not found")
    return await _serialize_ticket(db, dict(row), detail=True)


@router.post("/tickets")
async def create_ticket(body: TicketCreateIn, user: dict = Me) -> dict:
    db = await get_db()

    # Resolve / create customer
    cust_id = body.customerId
    if not cust_id:
        if body.customer:
            cur = await db.execute(
                "SELECT id FROM customers WHERE workspace_id = ? AND name = ? LIMIT 1",
                (user["workspace_id"], body.customer),
            )
            row = await cur.fetchone()
            if row:
                cust_id = row[0]
        if not cust_id:
            cust_id = str(uuid4())
            await db.execute(
                "INSERT INTO customers (id, workspace_id, name, kind) VALUES (?,?,?, 'customer')",
                (cust_id, user["workspace_id"], body.customer or "New Customer"),
            )

    channel = body.channel or body.source
    sla_due = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    cur = await db.execute(
        """
        INSERT INTO tickets (workspace_id, customer_id, source, channel, task, team,
                             priority, subject, description, tags, sla_due_at, order_id,
                             custom_fields, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'queued')
        RETURNING id
        """,
        (user["workspace_id"], cust_id, body.source, channel, body.task, body.team,
         body.priority, body.subject, body.description, js(body.tags), sla_due,
         body.orderId, js({})),
    )
    tid = cur.lastrowid
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, actor_user_id, text) VALUES (?,?,?,?)",
        (tid, user["email"], user["id"], f"Ticket created — task: {body.task}"),
    )
    await db.commit()

    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (tid,))
    row = dict(await cur.fetchone())
    serialized = await _serialize_ticket(db, row, detail=True)
    await broker.publish(f"inbox:{user['workspace_id']}", "ticket.created", serialized)
    return serialized


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: str, body: TicketPatchIn, user: dict = Me) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE id = ? AND workspace_id = ?",
        (tid, user["workspace_id"]),
    )
    existing = await cur.fetchone()
    if not existing:
        raise HTTPException(404, "ticket not found")
    existing = dict(existing)

    updates: dict = {}
    timeline: list[str] = []
    if body.status is not None and body.status != existing["status"]:
        updates["status"] = body.status
        timeline.append(f"Status changed: {existing['status']} → {body.status}")
    if body.team is not None and body.team != existing["team"]:
        updates["team"] = body.team
        timeline.append(f"Team set to {body.team}")
    if body.task is not None and body.task != existing["task"]:
        updates["task"] = body.task
        timeline.append(f"Task set to {body.task}")
    if body.source is not None and body.source != existing["source"]:
        updates["source"] = body.source
    if body.voc is not None:
        updates["voc"] = body.voc
    resolution = body.resolution_notes or body.resolutionNotes
    if resolution is not None:
        updates["resolution_notes"] = resolution
    if body.assigneeId is not None:
        updates["assignee_id"] = body.assigneeId
        name = await _get_user_name(db, body.assigneeId)
        timeline.append(f"Assigned to {name or body.assigneeId}")
    elif body.assignee is not None:
        cur = await db.execute(
            "SELECT id FROM users WHERE workspace_id = ? AND (name = ? OR email = ?)",
            (user["workspace_id"], body.assignee, body.assignee),
        )
        r = await cur.fetchone()
        if r:
            updates["assignee_id"] = r[0]
            timeline.append(f"Assigned to {body.assignee}")
    if body.customFields is not None:
        updates["custom_fields"] = js(body.customFields)

    if updates:
        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE tickets SET {sets} WHERE id = ?", (*updates.values(), tid)
        )
        for text in timeline:
            await db.execute(
                "INSERT INTO ticket_timeline (ticket_id, actor, actor_user_id, text) VALUES (?,?,?,?)",
                (tid, user["email"], user["id"], text),
            )
        await db.commit()

    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (tid,))
    row = dict(await cur.fetchone())
    out = await _serialize_ticket(db, row, detail=True)
    await broker.publish(f"ticket:{tid}", "ticket.updated", out)
    await broker.publish(f"inbox:{user['workspace_id']}", "ticket.updated", out)
    return out


@router.post("/tickets/{ticket_id}/notes")
async def add_ticket_note(ticket_id: str, body: dict, user: dict = Me) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    text = body.get("text", "")
    await db.execute(
        "INSERT INTO ticket_notes (ticket_id, author_id, author, text) VALUES (?,?,?,?)",
        (tid, user["id"], user["name"], text),
    )
    await db.commit()
    return {"author": user["name"], "at": _fmt_time(_now()), "text": text}


@router.post("/tickets/{ticket_id}/child")
async def create_child(ticket_id: str, body: ChildTicketIn, user: dict = Me) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    await db.execute(
        """
        INSERT INTO ticket_child (ticket_id, assignee_email, team, objective, prompt, resolution)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT (ticket_id) DO UPDATE SET
          assignee_email = EXCLUDED.assignee_email,
          team           = EXCLUDED.team,
          objective      = EXCLUDED.objective,
          prompt         = EXCLUDED.prompt,
          resolution     = EXCLUDED.resolution
        """,
        (tid, body.assigneeEmail, body.team, body.objective, body.prompt, body.resolution),
    )
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, ?, ?)",
        (tid, "system", f"Child ticket opened: {body.objective}"),
    )
    await db.commit()
    return body.model_dump()


@router.patch("/tickets/{ticket_id}/child")
async def patch_child(ticket_id: str, body: ChildTicketIn, user: dict = Me) -> dict:
    return await create_child(ticket_id, body, user)


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, body: TicketResolveIn, user: dict = Me) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    now = _now()
    await db.execute(
        """
        UPDATE tickets SET status='resolved', voc=?, resolution_notes=?, resolved_at=?, updated_at=?
        WHERE id = ? AND workspace_id = ?
        """,
        (body.voc, body.resolutionNotes, now, now, tid, user["workspace_id"]),
    )
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, actor_user_id, text) VALUES (?,?,?,?)",
        (tid, user["email"], user["id"], f"Resolved: {body.disposition or 'done'}"),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (tid,))
    row = dict(await cur.fetchone())
    out = await _serialize_ticket(db, row, detail=True)
    await broker.publish(f"ticket:{tid}", "ticket.updated", out)
    return out


@router.post("/tickets/similar")
async def similar_tickets(body: dict, user: dict = Me) -> list[dict]:
    # Build 1: simple ILIKE match. Build 2 swaps in pgvector HNSW. See spec §3.4.
    db = await get_db()
    q = (body.get("subject") or body.get("description") or "").strip()
    if not q:
        return []
    cur = await db.execute(
        """
        SELECT t.* FROM tickets t
        WHERE t.workspace_id = ? AND (t.subject ILIKE ? OR t.description ILIKE ? OR t.task ILIKE ?)
        ORDER BY t.created_at DESC LIMIT 10
        """,
        (user["workspace_id"], f"%{q}%", f"%{q}%", f"%{q}%"),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for r in rows:
        out.append({"ticket": await _serialize_ticket(db, r), "score": 0.6})
    return out
