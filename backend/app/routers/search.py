"""Global search (spec §2)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import get_db
from ..deps import Me

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(q: str = Query(""), limit: int = 8, user: dict = Me) -> dict:
    if not q.strip():
        return {"conversations": [], "tickets": [], "contacts": [], "total": 0}
    db = await get_db()
    like = f"%{q}%"

    cur = await db.execute(
        """
        SELECT c.id, c.preview, c.channel, cu.name
        FROM conversations c JOIN customers cu ON cu.id = c.customer_id
        WHERE c.workspace_id = ? AND (c.preview ILIKE ? OR cu.name ILIKE ?)
        ORDER BY c.last_message_at DESC NULLS LAST
        LIMIT ?
        """,
        (user["workspace_id"], like, like, limit),
    )
    conversations = [
        {
            "type": "conversation",
            "id": r["id"],
            "title": r["name"] or "Conversation",
            "subtitle": r["preview"] or (r["channel"] or ""),
        }
        for r in [dict(x) for x in await cur.fetchall()]
    ]

    cur = await db.execute(
        """
        SELECT t.id, t.subject, t.task, t.status, cu.name
        FROM tickets t LEFT JOIN customers cu ON cu.id = t.customer_id
        WHERE t.workspace_id = ? AND (t.subject ILIKE ? OR t.description ILIKE ? OR t.task ILIKE ? OR cu.name ILIKE ?)
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (user["workspace_id"], like, like, like, like, limit),
    )
    tickets = []
    for r in [dict(x) for x in await cur.fetchall()]:
        sub_parts = [p for p in (r["name"], r["status"]) if p]
        tickets.append({
            "type": "ticket",
            "id": f"#{r['id']}",
            "title": r["subject"] or r["task"] or f"Ticket #{r['id']}",
            "subtitle": " · ".join(sub_parts),
        })

    cur = await db.execute(
        """
        SELECT id, name, email, phone FROM customers
        WHERE workspace_id = ? AND (name ILIKE ? OR email ILIKE ? OR phone ILIKE ?)
        LIMIT ?
        """,
        (user["workspace_id"], like, like, like, limit),
    )
    contacts = []
    for r in [dict(x) for x in await cur.fetchall()]:
        contacts.append({
            "type": "contact",
            "id": r["id"],
            "title": r["name"] or r["email"] or r["phone"] or "Contact",
            "subtitle": r["email"] or r["phone"] or "",
        })

    # Teammates (workspace members) so the top-bar search can start a DM with
    # anyone in the org. We exclude the caller — searching yourself and then
    # hitting Enter would just open your own notes-to-self, which is not what
    # people expect from this action.
    cur = await db.execute(
        """
        SELECT id, name, email, role FROM users
        WHERE workspace_id = ? AND id <> ? AND (name ILIKE ? OR email ILIKE ?)
        ORDER BY name
        LIMIT ?
        """,
        (user["workspace_id"], user["id"], like, like, limit),
    )
    teammates = []
    for r in [dict(x) for x in await cur.fetchall()]:
        teammates.append({
            "type": "teammate",
            "id": r["id"],
            "title": r["name"] or r["email"] or "Teammate",
            "subtitle": r["role"] or r["email"] or "",
        })

    return {
        "conversations": conversations,
        "tickets": tickets,
        "contacts": contacts,
        "teammates": teammates,
        "total": len(conversations) + len(tickets) + len(contacts) + len(teammates),
    }
