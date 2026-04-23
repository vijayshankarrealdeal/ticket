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
        LIMIT ?
        """,
        (user["workspace_id"], like, like, limit),
    )
    conversations = [
        {"id": r["id"], "name": r["name"], "preview": r["preview"], "channel": r["channel"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]

    cur = await db.execute(
        """
        SELECT t.id, t.task, t.status, cu.name
        FROM tickets t LEFT JOIN customers cu ON cu.id = t.customer_id
        WHERE t.workspace_id = ? AND (t.subject ILIKE ? OR t.description ILIKE ? OR cu.name ILIKE ?)
        LIMIT ?
        """,
        (user["workspace_id"], like, like, like, limit),
    )
    tickets = [
        {"id": f"#{r['id']}", "customer": r["name"], "task": r["task"], "status": r["status"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]

    cur = await db.execute(
        """
        SELECT id, name, email, phone FROM customers
        WHERE workspace_id = ? AND (name ILIKE ? OR email ILIKE ? OR phone ILIKE ?)
        LIMIT ?
        """,
        (user["workspace_id"], like, like, like, limit),
    )
    contacts = [dict(r) for r in await cur.fetchall()]

    return {
        "conversations": conversations,
        "tickets": tickets,
        "contacts": contacts,
        "total": len(conversations) + len(tickets) + len(contacts),
    }
