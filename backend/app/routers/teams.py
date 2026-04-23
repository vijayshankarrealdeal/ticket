"""Teams internal chat (spec §3.6)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from ..db import get_db, jl, js
from ..deps import Me
from ..schemas import ReactionIn, TeamMessageIn
from ..ws import broker

router = APIRouter(prefix="/teams", tags=["teams"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/members")
async def members(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, email, role, status, avatar_url FROM users WHERE workspace_id = ? ORDER BY name",
        (user["workspace_id"],),
    )
    return [dict(r) for r in await cur.fetchall()]


@router.get("/channels")
async def channels(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, description, privacy FROM teams_channels WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for c in rows:
        cur2 = await db.execute(
            "SELECT COUNT(*) FROM teams_messages WHERE channel_id = ?", (c["id"],)
        )
        count = (await cur2.fetchone())[0] or 0
        out.append({**c, "messageCount": count})
    return out


@router.post("/channels")
async def create_channel(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cid = str(uuid4())
    await db.execute(
        "INSERT INTO teams_channels (id, workspace_id, name, description, privacy) VALUES (?,?,?,?,?)",
        (cid, user["workspace_id"], body.get("name", "new-channel"),
         body.get("description", ""), body.get("privacy", "private")),
    )
    await db.commit()
    return {"id": cid, "name": body.get("name", "new-channel")}


@router.get("/dms")
async def dms(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, member_ids FROM teams_dms WHERE workspace_id = ?", (user["workspace_id"],)
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [{"id": r["id"], "members": jl(r["member_ids"], [])} for r in rows]


@router.post("/dms")
async def create_dm(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    members = body.get("members", [])
    if user["id"] not in members:
        members.append(user["id"])
    did = str(uuid4())
    await db.execute(
        "INSERT INTO teams_dms (id, workspace_id, member_ids) VALUES (?,?,?)",
        (did, user["workspace_id"], js(members)),
    )
    await db.commit()
    return {"id": did, "members": members}


async def _is_channel(db, cid: str) -> bool:
    cur = await db.execute("SELECT 1 FROM teams_channels WHERE id = ?", (cid,))
    return bool(await cur.fetchone())


async def _serialize_tmsg(db, m: dict) -> dict:
    cur = await db.execute(
        "SELECT emoji, COUNT(*) FROM teams_reactions WHERE message_id = ? GROUP BY emoji",
        (m["id"],),
    )
    reactions = {emoji: count for (emoji, count) in await cur.fetchall()}
    return {
        "id": m["id"],
        "userId": m["user_id"],
        "text": m.get("text") or "",
        "blocks": jl(m.get("blocks"), []),
        "attachments": jl(m.get("attachments"), []),
        "replyTo": m.get("reply_to_id"),
        "ts": m["at"],
        "reaction": reactions,
    }


@router.get("/conversations/{cid}/messages")
async def conversation_messages(cid: str, user: dict = Me) -> list[dict]:
    db = await get_db()
    is_channel = await _is_channel(db, cid)
    if is_channel:
        cur = await db.execute(
            "SELECT * FROM teams_messages WHERE channel_id = ? ORDER BY at", (cid,)
        )
    else:
        cur = await db.execute(
            "SELECT * FROM teams_messages WHERE dm_id = ? ORDER BY at", (cid,)
        )
    rows = [dict(r) for r in await cur.fetchall()]
    return [await _serialize_tmsg(db, m) for m in rows]


@router.post("/conversations/{cid}/messages")
async def post_team_message(cid: str, body: TeamMessageIn, user: dict = Me) -> dict:
    db = await get_db()
    mid = str(uuid4())
    is_channel = await _is_channel(db, cid)
    await db.execute(
        """
        INSERT INTO teams_messages (id, workspace_id, channel_id, dm_id, user_id,
                                    text, blocks, attachments, reply_to_id)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (mid, user["workspace_id"], cid if is_channel else None, None if is_channel else cid,
         user["id"], body.text, js(body.blocks), js(body.attachments), body.replyTo),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM teams_messages WHERE id = ?", (mid,))
    row = dict(await cur.fetchone())
    msg = await _serialize_tmsg(db, row)
    await broker.publish(f"teams:{cid}", "team.message.created", msg)
    return msg


@router.post("/messages/{message_id}/reactions")
async def toggle_reaction(message_id: str, body: ReactionIn, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM teams_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
        (message_id, user["id"], body.emoji),
    )
    if await cur.fetchone():
        await db.execute(
            "DELETE FROM teams_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user["id"], body.emoji),
        )
    else:
        await db.execute(
            "INSERT INTO teams_reactions (message_id, user_id, emoji) VALUES (?,?,?)",
            (message_id, user["id"], body.emoji),
        )
    await db.commit()
    cur = await db.execute("SELECT * FROM teams_messages WHERE id = ?", (message_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "message not found")
    return await _serialize_tmsg(db, dict(row))


@router.post("/typing/{cid}")
async def typing(cid: str, body: dict, user: dict = Me) -> dict:
    await broker.publish(
        f"teams:{cid}", "team.typing",
        {"user_id": user["id"], "name": user["name"], "on": body.get("on", True)},
    )
    return {"ok": True}


# ---------- read cursors + unread badges (spec §3.6 teams_read_cursors) ----------

@router.post("/conversations/{cid}/read")
async def mark_read(cid: str, user: dict = Me) -> dict:
    """Record the caller's last-read time on a channel or DM so unread badges
    can be derived without O(messages) scans."""
    db = await get_db()
    is_channel = await _is_channel(db, cid)
    now = _now()
    if is_channel:
        await db.execute(
            """
            INSERT INTO teams_read_cursors (user_id, channel_id, last_read_at)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id, channel_id) WHERE channel_id IS NOT NULL
            DO UPDATE SET last_read_at = EXCLUDED.last_read_at
            """,
            (user["id"], cid, now),
        )
    else:
        await db.execute(
            """
            INSERT INTO teams_read_cursors (user_id, dm_id, last_read_at)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id, dm_id) WHERE dm_id IS NOT NULL
            DO UPDATE SET last_read_at = EXCLUDED.last_read_at
            """,
            (user["id"], cid, now),
        )
    await db.commit()
    return {"ok": True, "at": now}


@router.get("/unread")
async def teams_unread(user: dict = Me) -> dict:
    """Per-channel + per-DM unread counts for the left-rail badges. Also
    returns an aggregate for the TopBar."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT ch.id, COUNT(m.id) AS unread
          FROM teams_channels ch
          LEFT JOIN teams_read_cursors r
            ON r.channel_id = ch.id AND r.user_id = ?
          LEFT JOIN teams_messages m
            ON m.channel_id = ch.id AND m.user_id <> ?
           AND m.at > COALESCE(r.last_read_at, '1970-01-01'::timestamptz)
         WHERE ch.workspace_id = ?
         GROUP BY ch.id
        """,
        (user["id"], user["id"], user["workspace_id"]),
    )
    channels_unread = {r["id"]: int(r["unread"] or 0) for r in [dict(x) for x in await cur.fetchall()]}
    cur = await db.execute(
        """
        SELECT d.id, COUNT(m.id) AS unread
          FROM teams_dms d
          LEFT JOIN teams_read_cursors r
            ON r.dm_id = d.id AND r.user_id = ?
          LEFT JOIN teams_messages m
            ON m.dm_id = d.id AND m.user_id <> ?
           AND m.at > COALESCE(r.last_read_at, '1970-01-01'::timestamptz)
         WHERE d.workspace_id = ?
         GROUP BY d.id
        """,
        (user["id"], user["id"], user["workspace_id"]),
    )
    dms_unread = {r["id"]: int(r["unread"] or 0) for r in [dict(x) for x in await cur.fetchall()]}
    total = sum(channels_unread.values()) + sum(dms_unread.values())
    return {"channels": channels_unread, "dms": dms_unread, "total": total}


# ---------- Presence (spec §3.6) ----------

@router.get("/presence")
async def presence(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, name, status, last_seen_at FROM users
         WHERE workspace_id = ? ORDER BY name
        """,
        (user["workspace_id"],),
    )
    now = datetime.now(timezone.utc)
    out = []
    for r in [dict(x) for x in await cur.fetchall()]:
        # Anyone whose last heartbeat is > 2 min old is effectively offline.
        last = r.get("last_seen_at")
        status = r["status"] or "offline"
        if last:
            try:
                dt = last if isinstance(last, datetime) else datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt) > timedelta(minutes=2) and status != "offline":
                    status = "away"
            except Exception:
                pass
        out.append({"id": r["id"], "name": r["name"], "status": status, "lastSeenAt": last})
    return out


@router.post("/presence/heartbeat")
async def presence_heartbeat(body: dict | None = None, user: dict = Me) -> dict:
    """Tab calls this every 30s to stay 'online'. The reaper worker flips users
    offline when heartbeats stop — see worker.py."""
    db = await get_db()
    status = (body or {}).get("status") or "online"
    await db.execute(
        "UPDATE users SET last_seen_at = now(), status = ? WHERE id = ?",
        (status, user["id"]),
    )
    await db.commit()
    await broker.publish(
        f"workspace:{user['workspace_id']}", "presence.changed",
        {"userId": user["id"], "status": status},
    )
    return {"ok": True, "status": status}
