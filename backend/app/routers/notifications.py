"""Notifications (spec §3.7)."""
from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..deps import Me
from ..ws import broker

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _serialize(n: dict) -> dict:
    return {
        "id": n["id"],
        "type": n["type"],
        "title": n["title"],
        "body": n.get("body"),
        "time": n["ts"],
        "ts": n["ts"],
        "unread": bool(n["unread"]),
        "link": n.get("link"),
    }


@router.get("")
async def list_notifs(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY ts DESC LIMIT 200",
        (user["id"],),
    )
    return [_serialize(dict(r)) for r in await cur.fetchall()]


@router.post("/{nid}/read")
async def mark_read(nid: int, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET unread = 0 WHERE id = ? AND user_id = ?",
        (nid, user["id"]),
    )
    await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def read_all(user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET unread = 0 WHERE user_id = ?", (user["id"],)
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{nid}")
async def delete_one(nid: int, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM notifications WHERE id = ? AND user_id = ?", (nid, user["id"])
    )
    await db.commit()
    return {"ok": True}


@router.delete("")
async def clear_all(user: dict = Me) -> dict:
    db = await get_db()
    await db.execute("DELETE FROM notifications WHERE user_id = ?", (user["id"],))
    await db.commit()
    return {"ok": True}


async def create_notification(user_id: str, type_: str, title: str, body: str | None = None, link: str | None = None) -> None:
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO notifications (user_id, type, title, body, link) VALUES (?,?,?,?,?) RETURNING id",
        (user_id, type_, title, body, link),
    )
    nid = cur.lastrowid
    cur = await db.execute("SELECT * FROM notifications WHERE id = ?", (nid,))
    await broker.publish(f"notif:{user_id}", "notification.created", _serialize(dict(await cur.fetchone())))
