"""Teams internal chat (spec §3.6)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from ..db import get_db, jl, js
from ..deps import Me
from ..schemas import ReactionIn, TeamMessageIn
from ..ws import broker
from .notifications import create_notification

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


async def _workspace_member_ids(db, workspace_id: str) -> list[str]:
    cur = await db.execute(
        "SELECT id FROM users WHERE workspace_id = ? ORDER BY created_at NULLS LAST, id",
        (workspace_id,),
    )
    return [str(r["id"]) for r in await cur.fetchall()]


@router.get("/channels")
async def channels(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, description, privacy FROM teams_channels WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    # Channels are workspace-open in the current schema (no per-channel members
    # table), so "members" is the full workspace roster. The frontend dereferences
    # `ch.members` in the chat header + info pane, so returning an empty list
    # would blank out the avatar stack.
    members = await _workspace_member_ids(db, user["workspace_id"])
    out = []
    for c in rows:
        cur2 = await db.execute(
            "SELECT COUNT(*) FROM teams_messages WHERE channel_id = ?", (c["id"],)
        )
        count = (await cur2.fetchone())[0] or 0
        out.append({**c, "messageCount": count, "members": members})
    return out


@router.post("/channels")
async def create_channel(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cid = str(uuid4())
    name = (body.get("name") or "new-channel").strip().lower() or "new-channel"
    description = body.get("description") or ""
    privacy = body.get("privacy") or "private"
    try:
        await db.execute(
            "INSERT INTO teams_channels (id, workspace_id, name, description, privacy) VALUES (?,?,?,?,?)",
            (cid, user["workspace_id"], name, description, privacy),
        )
        await db.commit()
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, "A channel with that name already exists")
        raise
    members = await _workspace_member_ids(db, user["workspace_id"])
    return {
        "id": cid,
        "name": name,
        "description": description,
        "privacy": privacy,
        "messageCount": 0,
        "members": members,
    }


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


# Matches `@Name` and `@First Last` (a second word must itself be Capitalized
# so we don't swallow following lowercase prose: `@Priya and @Raj` stays two).
_MENTION_RE = re.compile(r"@([A-Z][A-Za-z0-9_.-]*(?:[ ][A-Z][A-Za-z0-9_.-]*)?|[a-z][a-z0-9_.-]*)")
# `#channel-name` references — Slack-style. Lowercase + `-`/`_` only so it
# doesn't collide with @user mentions or hex colours pasted into messages.
_CHANNEL_RE = re.compile(r"#([a-z0-9][a-z0-9_-]{1,40})")
_REL_FMT_RECENT = timedelta(seconds=60)


def _fmt_team_time(ts) -> str:
    """Frontend displays `at` as a human string ('Just now', '10:42 AM', etc.)."""
    if not ts:
        return ""
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    if delta < _REL_FMT_RECENT:
        return "Just now"
    if delta < timedelta(hours=24):
        return dt.astimezone().strftime("%I:%M %p").lstrip("0")
    return dt.astimezone().strftime("%b %d, %I:%M %p").replace(" 0", " ")


async def _reply_snapshot(db, reply_to_id: str | None) -> dict | None:
    if not reply_to_id:
        return None
    cur = await db.execute(
        "SELECT id, user_id, text, blocks FROM teams_messages WHERE id = ?",
        (reply_to_id,),
    )
    r = await cur.fetchone()
    if not r:
        return None
    r = dict(r)
    preview = (r.get("text") or "").strip()
    if not preview:
        blocks = jl(r.get("blocks"), [])
        for b in blocks:
            if b.get("type") == "text" and b.get("value"):
                preview = b["value"]; break
    return {
        "messageId": r["id"],
        "userId": r["user_id"],
        "preview": (preview[:117] + "…") if len(preview) > 120 else preview,
    }


async def _serialize_tmsg(db, m: dict) -> dict:
    cur = await db.execute(
        "SELECT emoji, user_id FROM teams_reactions WHERE message_id = ?",
        (m["id"],),
    )
    grouped: dict[str, list[str]] = {}
    for row in await cur.fetchall():
        emoji, uid = (row["emoji"], row["user_id"]) if isinstance(row, dict) else (row[0], row[1])
        grouped.setdefault(emoji, []).append(uid)
    reactions = [{"emoji": e, "userIds": uids} for e, uids in grouped.items()]
    reply_to = await _reply_snapshot(db, m.get("reply_to_id"))
    return {
        "id": m["id"],
        "userId": m["user_id"],
        "text": m.get("text") or "",
        "blocks": jl(m.get("blocks"), []),
        "attachments": jl(m.get("attachments"), []),
        "replyTo": reply_to,
        "at": _fmt_team_time(m.get("at")),
        "ts": m["at"],
        "reactions": reactions,
    }


async def _resolve_mentions(db, workspace_id: str, text: str | None, blocks: list | None) -> list[str]:
    """Find user IDs mentioned in a message body. Looks at any `{type:"mention",userId}`
    blocks the client sent *and* at `@Name` substrings in raw text — either is
    a valid mention because clients don't always parse before sending."""
    mentioned: set[str] = set()
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "mention":
            uid = b.get("userId")
            if uid:
                mentioned.add(str(uid))
    if text:
        names = {m.strip().rstrip(".,!?") for m in _MENTION_RE.findall(text)}
        names = {n for n in names if n}
        if names:
            params = [workspace_id, *names]
            placeholders = ",".join(["?"] * len(names))
            cur = await db.execute(
                f"SELECT id, name FROM users WHERE workspace_id = ? AND name IN ({placeholders})",
                params,
            )
            for r in await cur.fetchall():
                r = dict(r)
                mentioned.add(str(r["id"]))
    return list(mentioned)


async def _resolve_channel_mentions(db, workspace_id: str, text: str | None,
                                     blocks: list | None) -> list[dict]:
    """Resolve `#channel` references — both `{type:"channel_mention",channelId}`
    blocks and `#name` substrings — to a list of `{id, name}` channel rows in
    the same workspace. Used to fan a `team.channel.mentioned` event to every
    referenced channel so members with the channel open see the highlight."""
    ids: set[str] = set()
    names: set[str] = set()
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "channel_mention":
            cid = b.get("channelId")
            if cid:
                ids.add(str(cid))
    if text:
        for m in _CHANNEL_RE.findall(text):
            names.add(m.lower())
    if not ids and not names:
        return []
    rows: dict[str, dict] = {}
    if ids:
        ph = ",".join(["?"] * len(ids))
        cur = await db.execute(
            f"SELECT id, name FROM teams_channels WHERE workspace_id = ? AND id IN ({ph})",
            [workspace_id, *ids],
        )
        for r in await cur.fetchall():
            r = dict(r)
            rows[r["id"]] = r
    if names:
        ph = ",".join(["?"] * len(names))
        cur = await db.execute(
            f"SELECT id, name FROM teams_channels WHERE workspace_id = ? AND lower(name) IN ({ph})",
            [workspace_id, *names],
        )
        for r in await cur.fetchall():
            r = dict(r)
            rows[r["id"]] = r
    return list(rows.values())


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
    reply_to_id = None
    if body.replyTo:
        # Clients pass the raw id in `replyTo` (wire.md compat) or a snapshot-object
        # the caller already assembled locally.
        reply_to_id = body.replyTo if isinstance(body.replyTo, str) else body.replyTo.get("messageId")
    await db.execute(
        """
        INSERT INTO teams_messages (id, workspace_id, channel_id, dm_id, user_id,
                                    text, blocks, attachments, reply_to_id)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (mid, user["workspace_id"], cid if is_channel else None, None if is_channel else cid,
         user["id"], body.text, js(body.blocks), js(body.attachments), reply_to_id),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM teams_messages WHERE id = ?", (mid,))
    row = dict(await cur.fetchone())
    msg = await _serialize_tmsg(db, row)
    # Frontend uses `tempId` for optimistic dedupe — echo it back to the sender.
    if getattr(body, "tempId", None):
        msg["tempId"] = body.tempId

    await broker.publish(f"teams:{cid}", "team.message.created", msg)

    # Notify anyone mentioned in the message so their bell lights up (spec §3.7).
    mentioned = await _resolve_mentions(db, user["workspace_id"], body.text, body.blocks)
    if mentioned:
        scope = "#channel" if is_channel else "DM"
        for uid in mentioned:
            if uid == user["id"]:
                continue
            await create_notification(
                uid, "mention",
                title=f"{user['name']} mentioned you in {scope}",
                body=(body.text or "").strip()[:160],
                link=f"/teams?id={cid}",
            )

    # `#channel-name` cross-references — fan a lightweight event to every
    # referenced channel so anyone with the channel open sees a "mentioned in
    # #other-channel" pill, and persist a `channel_mention` notification so it
    # shows up in the bell.
    chan_refs = await _resolve_channel_mentions(db, user["workspace_id"], body.text, body.blocks)
    for ch in chan_refs:
        if ch["id"] == cid:
            continue
        await broker.publish(
            f"teams:{ch['id']}", "team.channel.mentioned",
            {"fromChannelId": cid, "fromUserId": user["id"], "fromName": user["name"],
             "messageId": mid, "text": (body.text or "")[:200]},
        )
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
    row = dict(row)
    msg = await _serialize_tmsg(db, row)
    topic_id = row.get("channel_id") or row.get("dm_id")
    if topic_id:
        await broker.publish(f"teams:{topic_id}", "team.reaction.updated", {
            "messageId": message_id,
            "reactions": msg["reactions"],
        })
    return msg


@router.post("/typing/{cid}")
async def typing(cid: str, body: dict | None = None, user: dict = Me) -> dict:
    on = (body or {}).get("on", True) if isinstance(body, dict) else True
    await broker.publish(
        f"teams:{cid}", "team.typing",
        {"userId": user["id"], "userName": user["name"], "on": on},
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
