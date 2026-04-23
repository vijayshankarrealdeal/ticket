"""Tickets (spec §3.4)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query

from ..db import get_db, jl, js
from ..deps import AgentOrAdmin, Me
from ..idempotency import check_idempotency, save_idempotency
from ..schemas import (
    ChildTicketIn,
    TicketCreateIn,
    TicketPatchIn,
    TicketResolveIn,
)
from ..ws import broker
from .notifications import create_notification

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


async def _sla_hours(db, workspace_id: str, team: str | None, task: str | None,
                     priority: str | None) -> float:
    """Resolve SLA hours from `sla_policies` by most-specific match first. Falls
    back to 24h when no policy is configured."""
    cur = await db.execute(
        """
        SELECT hours FROM sla_policies
         WHERE workspace_id = ?
           AND (team IS NULL OR team = ?)
           AND (task IS NULL OR task = ?)
           AND (priority IS NULL OR priority = ?)
         ORDER BY (team IS NOT NULL)::int + (task IS NOT NULL)::int + (priority IS NOT NULL)::int DESC
         LIMIT 1
        """,
        (workspace_id, team, task, priority),
    )
    row = await cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return 24.0


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
        "conversationId": t.get("conversation_id"),
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

    conv_id = t.get("conversation_id")
    if conv_id:
        cur = await db.execute(
            """
            SELECT author, at, text FROM ticket_notes WHERE ticket_id = ?
            UNION ALL
            SELECT author_name AS author, at, text FROM conversation_notes WHERE conversation_id = ?
            ORDER BY at
            """,
            (t["id"], conv_id),
        )
    else:
        cur = await db.execute(
            "SELECT author, at, text FROM ticket_notes WHERE ticket_id = ? ORDER BY at",
            (t["id"],),
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
async def create_ticket(
    body: TicketCreateIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: dict = AgentOrAdmin,
) -> dict:
    cached = await check_idempotency(user, "tickets.create", idempotency_key)
    if cached is not None:
        return cached
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
    sla_due = (datetime.now(timezone.utc) + timedelta(hours=await _sla_hours(db, user["workspace_id"], body.team, body.task, body.priority))).isoformat()

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
    await save_idempotency(user, "tickets.create", idempotency_key, serialized)
    return serialized


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: str, body: TicketPatchIn, user: dict = AgentOrAdmin) -> dict:
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
    # Distinguish "field omitted" (leave alone) from "assigneeId: null"
    # (explicit unassign). Pydantic defaults both to None, so we peek at
    # `model_fields_set` which only lists keys actually present in the body.
    provided = body.model_fields_set
    if "assigneeId" in provided:
        if body.assigneeId:
            updates["assignee_id"] = body.assigneeId
            name = await _get_user_name(db, body.assigneeId)
            timeline.append(f"Assigned to {name or body.assigneeId}")
        else:
            updates["assignee_id"] = None
            prev_name = await _get_user_name(db, existing.get("assignee_id"))
            timeline.append(f"Unassigned from {prev_name or 'previous assignee'}")
    elif "assignee" in provided:
        if body.assignee:
            cur = await db.execute(
                "SELECT id FROM users WHERE workspace_id = ? AND (name = ? OR email = ?)",
                (user["workspace_id"], body.assignee, body.assignee),
            )
            r = await cur.fetchone()
            if r:
                updates["assignee_id"] = r[0]
                timeline.append(f"Assigned to {body.assignee}")
        else:
            updates["assignee_id"] = None
            timeline.append("Unassigned")
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

    # Notify new assignee (if changed) — spec §3.7 "Generators".
    new_assignee = updates.get("assignee_id")
    if new_assignee and new_assignee != existing.get("assignee_id") and new_assignee != user["id"]:
        await create_notification(
            new_assignee, "assign",
            title=f"Ticket #{tid} assigned to you",
            body=existing.get("subject") or existing.get("task"),
            link=f"/tickets?id={tid}",
        )

    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (tid,))
    row = dict(await cur.fetchone())
    out = await _serialize_ticket(db, row, detail=True)
    await broker.publish(f"ticket:{tid}", "ticket.updated", out)
    await broker.publish(f"inbox:{user['workspace_id']}", "ticket.updated", out)
    await broker.publish(f"ticket:{tid}", "ticket.timeline.appended", {"ticketId": tid})
    return out


@router.post("/tickets/{ticket_id}/notes")
async def add_ticket_note(ticket_id: str, body: dict, user: dict = AgentOrAdmin) -> dict:
    tid = _parse_ticket_id(ticket_id)
    db = await get_db()
    text = body.get("text", "")
    cur = await db.execute(
        "SELECT conversation_id FROM tickets WHERE id = ? AND workspace_id = ?",
        (tid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "ticket not found")
    conv_id = dict(row).get("conversation_id")
    if conv_id:
        await db.execute(
            """
            INSERT INTO conversation_notes (id, conversation_id, author_id, author_name, text)
            VALUES (?,?,?,?,?)
            """,
            (str(uuid4()), conv_id, user["id"], user["name"], text),
        )
    else:
        await db.execute(
            "INSERT INTO ticket_notes (ticket_id, author_id, author, text) VALUES (?,?,?,?)",
            (tid, user["id"], user["name"], text),
        )
    await db.commit()
    return {"author": user["name"], "at": _fmt_time(_now()), "text": text}


@router.post("/tickets/{ticket_id}/child")
async def create_child(ticket_id: str, body: ChildTicketIn, user: dict = AgentOrAdmin) -> dict:
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
async def patch_child(ticket_id: str, body: ChildTicketIn, user: dict = AgentOrAdmin) -> dict:
    return await create_child(ticket_id, body, user)


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, body: TicketResolveIn, user: dict = AgentOrAdmin) -> dict:
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


@router.post("/tickets/{ticket_id}/merge")
async def merge_ticket(ticket_id: str, body: dict, user: dict = AgentOrAdmin) -> dict:
    """Merge `intoTicketId` into `ticket_id`. Moves messages + timeline across,
    closes the source (spec §3.4.1)."""
    src_id = _parse_ticket_id(ticket_id)
    into_id = body.get("intoTicketId")
    if not into_id:
        raise HTTPException(400, "intoTicketId required")
    into_id = _parse_ticket_id(str(into_id))
    db = await get_db()
    await db.execute("UPDATE messages SET ticket_id = ? WHERE ticket_id = ?", (into_id, src_id))
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
        (into_id, f"Merged ticket #{src_id} in"),
    )
    await db.execute(
        "UPDATE tickets SET status = 'closed', resolution_notes = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
        (f"Merged into #{into_id}", _now(), _now(), src_id),
    )
    await db.commit()
    await broker.publish(f"inbox:{user['workspace_id']}", "ticket.updated", {"id": f"#{src_id}", "status": "closed"})
    await broker.publish(f"ticket:{into_id}", "ticket.timeline.appended", {"ticketId": into_id})
    return {"ok": True, "merged": src_id, "into": into_id}


@router.post("/tickets/{ticket_id}/attach-message")
async def attach_message(ticket_id: str, body: dict, user: dict = AgentOrAdmin) -> dict:
    """Manual override: move a single message under a different ticket (spec
    §3.4.1). Useful when the intent classifier guesses wrong."""
    tid = _parse_ticket_id(ticket_id)
    mid = body.get("messageId")
    if not mid:
        raise HTTPException(400, "messageId required")
    db = await get_db()
    await db.execute("UPDATE messages SET ticket_id = ? WHERE id = ?", (tid, mid))
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, actor_user_id, text) VALUES (?,?,?,?)",
        (tid, user["email"], user["id"], f"Message {mid} attached manually"),
    )
    await db.commit()
    await broker.publish(f"ticket:{tid}", "ticket.timeline.appended", {"ticketId": tid})
    return {"ok": True}


@router.post("/tickets/{ticket_id}/split")
async def split_ticket(ticket_id: str, body: dict, user: dict = AgentOrAdmin) -> dict:
    """Split the ticket in two starting at `fromMessageId` (spec §3.4.1).
    Creates a sibling ticket inheriting core metadata and re-points the tail
    messages to it."""
    tid = _parse_ticket_id(ticket_id)
    from_mid = body.get("fromMessageId")
    if not from_mid:
        raise HTTPException(400, "fromMessageId required")
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE id = ? AND workspace_id = ?",
        (tid, user["workspace_id"]),
    )
    src = await cur.fetchone()
    if not src:
        raise HTTPException(404, "ticket not found")
    src = dict(src)
    cur = await db.execute("SELECT at FROM messages WHERE id = ?", (from_mid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "message not found")
    split_at = row[0]

    cur = await db.execute(
        """
        INSERT INTO tickets (workspace_id, customer_id, conversation_id, source, channel,
                             task, team, assignee_id, priority, subject, description,
                             tags, custom_fields, status, parent_ticket_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'queued', ?)
        RETURNING id
        """,
        (src["workspace_id"], src["customer_id"], src.get("conversation_id"),
         src["source"], src["channel"], src["task"], src["team"],
         src.get("assignee_id"), src.get("priority", "normal"),
         (src.get("subject") or "") + " (split)", src.get("description"),
         js(jl(src.get("tags"), [])), js({}), src["id"]),
    )
    new_id = cur.lastrowid
    await db.execute(
        "UPDATE messages SET ticket_id = ? WHERE conversation_id = ? AND at >= ?",
        (new_id, src.get("conversation_id"), split_at),
    )
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
        (new_id, f"Split from ticket #{src['id']} at {split_at}"),
    )
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
        (tid, f"Split into ticket #{new_id} from {split_at}"),
    )
    await db.commit()
    await broker.publish(f"inbox:{user['workspace_id']}", "ticket.created", {"id": f"#{new_id}"})
    return {"ok": True, "newTicketId": new_id}


# ---------- Intent classifier (spec §3.4.1 Stage 1) -------------------------

INTENT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("refund", "money back"),                 "refund_pending"),
    (("where is", "status", "tracking", "when will", "delivery"), "delivery_status_enquiry"),
    (("return", "exchange"),                   "return_request"),
    (("address", "relocate", "moved"),         "address_change"),
    (("broken", "defect", "quality", "damage"), "product_quality"),
    (("cancel",),                              "cancel_request"),
]


def _classify_stage_one(text: str) -> tuple[str, str | None, float, str]:
    """Deterministic Stage-1 router. Returns (intent_key, order_ref, confidence, reason)."""
    import re as _re
    t = (text or "").lower()
    m = _re.search(r"\b(\d{7,})\b", t)
    order_ref = m.group(1) if m else None
    for tokens, intent in INTENT_KEYWORDS:
        if any(tok in t for tok in tokens):
            return intent, order_ref, 0.8, f"matched keyword → {intent}"
    return "general_enquiry", order_ref, 0.4, "no keyword match"


async def _classify_stage_two(text: str, default_intent: str) -> tuple[str, float, str]:
    """Stage-2 LLM router for ambiguous Stage-1 results. Returns
    (intent_key, confidence, reason). No-ops (returns default_intent) when
    OPENAI_API_KEY / the `openai` package aren't available so the offline
    demo still works — the call is best-effort."""
    import os, json as _json
    if not os.getenv("OPENAI_API_KEY"):
        return default_intent, 0.4, "stage-2 skipped — no OPENAI_API_KEY"
    try:
        from openai import AsyncOpenAI  # type: ignore
    except Exception:
        return default_intent, 0.4, "stage-2 skipped — openai sdk missing"
    valid = [i for (_toks, i) in INTENT_KEYWORDS] + ["general_enquiry"]
    try:
        client = AsyncOpenAI()
        resp = await client.chat.completions.create(
            model=os.getenv("INTENT_MODEL", "gpt-4.1-mini"),
            temperature=0,
            messages=[
                {"role": "system",
                 "content": f"Classify the customer's message into one of: {', '.join(valid)}. "
                            "Respond with JSON {intent, confidence} only."},
                {"role": "user", "content": text or ""},
            ],
            response_format={"type": "json_object"},
        )
        payload = _json.loads(resp.choices[0].message.content or "{}")
        intent = str(payload.get("intent") or default_intent)
        if intent not in valid:
            intent = default_intent
        conf = float(payload.get("confidence") or 0.75)
        return intent, max(0.0, min(1.0, conf)), "stage-2 llm"
    except Exception as e:  # pragma: no cover — best-effort
        return default_intent, 0.4, f"stage-2 error: {e!r}"[:120]


@router.post("/internal/intent/classify")
async def intent_classify(body: dict, user: dict = Me) -> dict:
    """Server-internal classifier used by the inbound pipeline. Decides
    `attach | reopen | create` based on deterministic rules; Stage 2 LLM
    classifier is a Build-2 upgrade."""
    conv_id = body.get("conversationId")
    text = body.get("text") or ""
    if not conv_id:
        raise HTTPException(400, "conversationId required")
    intent, order_ref, confidence, reason = _classify_stage_one(text)
    # Escalate ambiguous Stage-1 results to the LLM when configured.
    if confidence < 0.6:
        stage2_intent, stage2_conf, stage2_reason = await _classify_stage_two(text, intent)
        if stage2_conf > confidence:
            intent, confidence, reason = stage2_intent, stage2_conf, f"stage-1 {reason}; {stage2_reason}"
    db = await get_db()
    # Candidate tickets: open ones on this conversation + resolved in last 7d.
    cur = await db.execute(
        """
        SELECT id, status, intent_key, order_ref, resolved_at
          FROM tickets
         WHERE conversation_id = ?
           AND (status NOT IN ('resolved','closed')
                OR resolved_at >= now() - interval '7 days')
         ORDER BY created_at DESC
        """,
        (conv_id,),
    )
    candidates = [dict(r) for r in await cur.fetchall()]
    decision = "create"
    ticket_id = None
    for c in candidates:
        if c.get("status") not in ("resolved", "closed"):
            same_intent = (c.get("intent_key") or "") == intent
            same_order = (c.get("order_ref") or None) == (order_ref or None)
            if same_intent and (same_order or not order_ref):
                decision, ticket_id = "attach", c["id"]
                break
        else:
            if (c.get("intent_key") or "") == intent and (c.get("order_ref") or None) == (order_ref or None):
                decision, ticket_id = "reopen", c["id"]
                break
    return {
        "decision": decision,
        "ticketId": ticket_id,
        "intentKey": intent,
        "orderRef": order_ref,
        "confidence": confidence,
        "reason": reason,
    }


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
