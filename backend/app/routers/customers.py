"""Customers (spec §3.5)."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db, jl
from ..deps import Me

router = APIRouter(tags=["customers"])


def _serialize_order(o: dict) -> dict:
    return {
        "id": o["id"],
        "time": o.get("placed_at"),
        "status": o.get("status"),
        "statusTone": o.get("status_tone") or "info",
        "total": o.get("total"),
        "itemsCount": sum(i.get("qty", 1) for i in jl(o.get("items"), [])),
        "items": jl(o.get("items"), []),
    }


async def _base_row(db, row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "orders": row.get("orders_count", 0),
        "returns": row.get("returns_count", 0),
        "since": row.get("since"),
        "address": row.get("address"),
        "otherAddresses": jl(row.get("other_addresses"), []),
    }


@router.get("/customers")
async def list_customers(
    q: str | None = Query(None),
    filter: str | None = Query(None, alias="filter"),
    sort: str | None = Query(None),
    shareable: int | None = Query(None),
    user: dict = Me,
) -> list[dict]:
    db = await get_db()
    clauses = ["workspace_id = ?"]
    params: list = [user["workspace_id"]]
    if q:
        clauses.append("(name ILIKE ? OR phone ILIKE ? OR email ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if filter == "with-orders":
        clauses.append("orders_count > 0")
    elif filter == "with-returns":
        clauses.append("returns_count > 0")
    sql = f"SELECT * FROM customers WHERE {' AND '.join(clauses)} ORDER BY name LIMIT 500"
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for r in rows:
        base = await _base_row(db, r)
        # count open tickets
        cur2 = await db.execute(
            "SELECT COUNT(*) FROM tickets WHERE customer_id = ? AND status NOT IN ('resolved','closed')",
            (r["id"],),
        )
        open_tickets = (await cur2.fetchone())[0] or 0
        base["openTickets"] = open_tickets
        out.append(base)
    return out


@router.get("/customers/search")
async def search_customer(
    q: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    shopifyId: str | None = None,
    name: str | None = None,
    user: dict = Me,
) -> list[dict]:
    db = await get_db()
    clauses = ["workspace_id = ?"]
    params: list = [user["workspace_id"]]
    if q:
        clauses.append("(name ILIKE ? OR phone ILIKE ? OR email ILIKE ? OR shopify_id ILIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if phone:
        clauses.append("phone = ?"); params.append(phone)
    if email:
        clauses.append("email = ?"); params.append(email.lower())
    if shopifyId:
        clauses.append("shopify_id = ?"); params.append(shopifyId)
    if name:
        clauses.append("name ILIKE ?"); params.append(f"%{name}%")
    sql = f"SELECT * FROM customers WHERE {' AND '.join(clauses)} LIMIT 20"
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    return [await _base_row(db, r) for r in rows]


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM customers WHERE id = ? AND workspace_id = ?",
        (customer_id, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "customer not found")
    base = await _base_row(db, dict(row))

    cur = await db.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT 10",
        (customer_id,),
    )
    base["recentOrders"] = [_serialize_order(dict(r)) for r in await cur.fetchall()]

    cur = await db.execute(
        "SELECT id, task, status, created_at FROM tickets WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20",
        (customer_id,),
    )
    base["tickets"] = [
        {"id": f"#{r['id']}", "task": r["task"], "status": r["status"], "at": r["created_at"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]
    return base


@router.post("/customers")
async def create_customer(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cid = str(uuid4())
    await db.execute(
        """
        INSERT INTO customers (id, workspace_id, name, phone, email, kind)
        VALUES (?,?,?,?,?, 'customer')
        """,
        (cid, user["workspace_id"], body.get("name"), body.get("phone"),
         (body.get("email") or "").lower() or None),
    )
    await db.commit()
    return {"id": cid, **body}


@router.patch("/customers/{customer_id}")
async def patch_customer(customer_id: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    allowed = {"name", "phone", "email", "address"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE customers SET {sets} WHERE id = ? AND workspace_id = ?",
            (*fields.values(), customer_id, user["workspace_id"]),
        )
        await db.commit()
    return await get_customer(customer_id, user)


@router.get("/customers/{customer_id}/recordings")
async def customer_recordings(customer_id: str, user: dict = Me) -> list[dict]:
    """Voice call recordings pulled from each conversation's jsonb blob.
    Real ingestion lands with the telephony adapter in Build 4 (spec §6.7)."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, channel, recordings FROM conversations
         WHERE customer_id = ? AND workspace_id = ?
           AND jsonb_array_length(COALESCE(recordings, '[]'::jsonb)) > 0
         ORDER BY last_message_at DESC
        """,
        (customer_id, user["workspace_id"]),
    )
    out: list[dict] = []
    for r in [dict(x) for x in await cur.fetchall()]:
        for rec in jl(r.get("recordings"), []):
            out.append({"conversationId": r["id"], "channel": r["channel"], **rec})
    return out


@router.get("/channels/for-customer/{customer_id}")
async def channels_for_customer(customer_id: str, user: dict = Me) -> list[dict]:
    """Channel switcher source (spec §3.3). Returns one row per channel the
    customer has been reachable on so the conversation header can hop between
    WhatsApp / Mail / Call for the same person."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT channel, MAX(last_message_at) AS last_at, COUNT(*) AS n
          FROM conversations
         WHERE customer_id = ? AND workspace_id = ?
         GROUP BY channel
         ORDER BY last_at DESC NULLS LAST
        """,
        (customer_id, user["workspace_id"]),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    # Enrich with the most recent conversation id per channel so the UI can
    # deep-link without a second round-trip.
    out: list[dict] = []
    for r in rows:
        cur2 = await db.execute(
            """
            SELECT id FROM conversations
             WHERE customer_id = ? AND workspace_id = ? AND channel = ?
             ORDER BY last_message_at DESC NULLS LAST LIMIT 1
            """,
            (customer_id, user["workspace_id"], r["channel"]),
        )
        row = await cur2.fetchone()
        out.append({
            "channel": r["channel"],
            "conversationId": row[0] if row else None,
            "lastAt": r.get("last_at"),
            "count": r.get("n"),
        })
    return out
