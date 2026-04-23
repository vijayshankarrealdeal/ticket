"""Teams internal chat (spec §3.6)."""
from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from ..db import get_db, jl, js
from ..deps import Me
from ..schemas import ReactionIn, TeamMessageIn
from ..ws import broker

router = APIRouter(prefix="/teams", tags=["teams"])


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
