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
        last = await _last_message_snapshot(db, channel_id=c["id"])
        out.append({**c, "messageCount": count, "members": members, **last})
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
    # Membership filter is load-bearing: without it, the sidebar would list
    # every DM in the workspace — including other users' self-DMs — so anyone
    # could open someone's "Notes to self" and post into it. `@>` is used
    # instead of the `?` jsonb operator because `?` collides with the db
    # wrapper's qmark→$N placeholder rewrite.
    cur = await db.execute(
        "SELECT id, member_ids FROM teams_dms WHERE workspace_id = ? AND member_ids @> ?::jsonb",
        (user["workspace_id"], js([user["id"]])),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    # Ensure a self-DM exists for the current user so the sidebar always has a
    # "(You)" scratchpad pinned at the top, matching the MS Teams pattern.
    me_ids = [user["id"]]
    has_self = any(jl(r["member_ids"], []) == me_ids for r in rows)
    if not has_self:
        did = str(uuid4())
        await db.execute(
            "INSERT INTO teams_dms (id, workspace_id, member_ids) VALUES (?,?,?)",
            (did, user["workspace_id"], js(me_ids)),
        )
        await db.commit()
        rows.insert(0, {"id": did, "member_ids": js(me_ids)})

    out = []
    for r in rows:
        last = await _last_message_snapshot(db, dm_id=r["id"])
        members = jl(r["member_ids"], [])
        is_self = len(members) == 1 and members[0] == user["id"]
        out.append({"id": r["id"], "members": members, "isSelf": is_self, **last})
    return out


@router.post("/dms")
async def create_dm(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    members = list({str(m) for m in body.get("members", []) if m})
    if user["id"] not in members:
        members.append(user["id"])
    # Every member must be in the caller's workspace — otherwise we'd let one
    # org DM a user in another org. Verify *all* ids resolve; any stranger is
    # treated as "not found".
    others = [m for m in members if m != user["id"]]
    if others:
        ph = ",".join(["?"] * len(others))
        cur = await db.execute(
            f"SELECT id FROM users WHERE workspace_id = ? AND id IN ({ph})",
            [user["workspace_id"], *others],
        )
        found = {str(r["id"]) if isinstance(r, dict) else str(r[0])
                 for r in await cur.fetchall()}
        if found != set(others):
            raise HTTPException(400, "one or more members are not in your organization")
    # Find-or-create: if a DM with exactly this member set already exists,
    # reuse it. Without this, two users each clicking "New chat" with each
    # other would create two parallel DMs and messages would fork by sender.
    cur = await db.execute(
        "SELECT id, member_ids FROM teams_dms "
        "WHERE workspace_id = ? AND member_ids @> ?::jsonb "
        "AND jsonb_array_length(member_ids) = ?",
        (user["workspace_id"], js(sorted(members)), len(members)),
    )
    for r in await cur.fetchall():
        r = dict(r)
        if set(jl(r.get("member_ids"), [])) == set(members):
            return {"id": r["id"], "members": members}
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


async def _assert_conversation_in_workspace(db, cid: str, workspace_id: str,
                                             user_id: str | None = None) -> bool:
    """Verify that a channel or DM id belongs to the caller's workspace and
    return whether it's a channel. Raises 404 otherwise — we never leak the
    existence of cross-workspace rows. Every teams endpoint that takes a
    conversation id as a path param must gate on this check, otherwise a
    user could read / write into another org's chat.

    When `user_id` is provided, DMs additionally require the caller to be a
    member. Channels are workspace-open so the membership check does not
    apply there. Without this, any workspace user could post into anyone
    else's DM (including their self-DM / Notes-to-self)."""
    cur = await db.execute(
        "SELECT 1 FROM teams_channels WHERE id = ? AND workspace_id = ?",
        (cid, workspace_id),
    )
    if await cur.fetchone():
        return True
    if user_id:
        cur = await db.execute(
            "SELECT 1 FROM teams_dms WHERE id = ? AND workspace_id = ? AND member_ids @> ?::jsonb",
            (cid, workspace_id, js([user_id])),
        )
    else:
        cur = await db.execute(
            "SELECT 1 FROM teams_dms WHERE id = ? AND workspace_id = ?",
            (cid, workspace_id),
        )
    if await cur.fetchone():
        return False
    raise HTTPException(404, "conversation not found")


async def _last_message_snapshot(db, *, channel_id: str | None = None,
                                  dm_id: str | None = None) -> dict:
    """Return a `{preview, lastAt, lastUserId}` snapshot for list rendering.
    Preview falls back to attachment shorthand when the message body is empty."""
    if channel_id:
        cur = await db.execute(
            "SELECT text, blocks, attachments, user_id, at FROM teams_messages "
            "WHERE channel_id = ? ORDER BY at DESC LIMIT 1", (channel_id,),
        )
    elif dm_id:
        cur = await db.execute(
            "SELECT text, blocks, attachments, user_id, at FROM teams_messages "
            "WHERE dm_id = ? ORDER BY at DESC LIMIT 1", (dm_id,),
        )
    else:
        return {"preview": "", "lastAt": None, "lastUserId": None}
    row = await cur.fetchone()
    if not row:
        return {"preview": "", "lastAt": None, "lastUserId": None}
    r = dict(row)
    preview = (r.get("text") or "").strip()
    if not preview:
        for b in jl(r.get("blocks"), []):
            if b.get("type") == "text" and b.get("value"):
                preview = b["value"]; break
            if b.get("type") == "mention":
                preview = f"@{b.get('name') or 'mention'}"; break
    if not preview:
        atts = jl(r.get("attachments"), [])
        if atts:
            a = atts[0]
            k = a.get("kind")
            if k == "ticket": preview = f"🎫 Shared ticket"
            elif k == "customer": preview = f"👤 Shared customer"
            elif k == "image": preview = f"🖼 {a.get('name') or 'Image'}"
            elif k == "file": preview = f"📎 {a.get('name') or 'File'}"
    if len(preview) > 80:
        preview = preview[:77] + "…"
    return {
        "preview": preview,
        "lastAt": _fmt_sidebar_time(r.get("at")),
        "lastAtIso": r.get("at"),
        "lastUserId": r.get("user_id"),
    }


def _fmt_sidebar_time(ts) -> str:
    """MS Teams-style short timestamp for the chat list — `10:42 AM` today,
    `Yesterday`, `Mon`, or `5/3` for older."""
    if not ts:
        return ""
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    now = datetime.now(local.tzinfo)
    if local.date() == now.date():
        return local.strftime("%I:%M %p").lstrip("0")
    delta_days = (now.date() - local.date()).days
    if delta_days == 1:
        return "Yesterday"
    if delta_days < 7:
        return local.strftime("%a")
    return f"{local.month}/{local.day}"


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
        "editedAt": m.get("edited_at"),
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
    is_channel = await _assert_conversation_in_workspace(db, cid, user["workspace_id"], user["id"])
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
    is_channel = await _assert_conversation_in_workspace(db, cid, user["workspace_id"], user["id"])
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

    # Nudge every conversation participant (except the sender) on their
    # personal teams feed so their left-rail preview + unread badge refresh
    # without a reload. For channels this is the full workspace roster; for
    # DMs it's the member list stored on the row.
    if is_channel:
        recipients = await _workspace_member_ids(db, user["workspace_id"])
    else:
        cur = await db.execute("SELECT member_ids FROM teams_dms WHERE id = ?", (cid,))
        row = await cur.fetchone()
        recipients = jl(row.get("member_ids"), []) if row else []
    for uid in recipients:
        if uid == user["id"]:
            continue
        await broker.publish(
            f"teams-nudge:{uid}", "teams.conversation.updated",
            {"conversationId": cid, "kind": "channel" if is_channel else "dm",
             "fromUserId": user["id"]},
        )

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


@router.patch("/messages/{message_id}")
async def edit_team_message(message_id: str, body: dict, user: dict = Me) -> dict:
    """Edit a message's text/blocks. Only the original author can edit."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM teams_messages WHERE id = ? AND workspace_id = ?",
        (message_id, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "message not found")
    row = dict(row)
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "you can only edit your own messages")
    new_text = body.get("text")
    if new_text is None:
        new_text = row.get("text") or ""
    new_blocks = body.get("blocks")
    if not new_blocks:
        new_blocks = [{"type": "text", "value": new_text}]
    await db.execute(
        "UPDATE teams_messages SET text = ?, blocks = ?, edited_at = now() WHERE id = ?",
        (new_text, js(new_blocks), message_id),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM teams_messages WHERE id = ?", (message_id,))
    fresh = dict(await cur.fetchone())
    msg = await _serialize_tmsg(db, fresh)
    topic_id = fresh.get("channel_id") or fresh.get("dm_id")
    if topic_id:
        await broker.publish(f"teams:{topic_id}", "team.message.updated", msg)
    return msg


@router.delete("/messages/{message_id}")
async def delete_team_message(message_id: str, user: dict = Me) -> dict:
    """Delete a message. Authors can always delete their own. In a DM any
    participant may delete (DMs are private and small), so anyone affected by
    a stray message — e.g. one posted into their Notes-to-self by a prior
    cross-DM leak bug — can clean it up themselves. In channels only the
    author may delete; an admin override isn't modeled here yet."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM teams_messages WHERE id = ? AND workspace_id = ?",
        (message_id, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "message not found")
    row = dict(row)
    allowed = row["user_id"] == user["id"]
    if not allowed and row.get("dm_id"):
        cur = await db.execute(
            "SELECT 1 FROM teams_dms WHERE id = ? AND workspace_id = ? AND member_ids @> ?::jsonb",
            (row["dm_id"], user["workspace_id"], js([user["id"]])),
        )
        if await cur.fetchone():
            allowed = True
    if not allowed:
        raise HTTPException(403, "not permitted")
    topic_id = row.get("channel_id") or row.get("dm_id")
    await db.execute("DELETE FROM teams_messages WHERE id = ?", (message_id,))
    await db.commit()
    if topic_id:
        await broker.publish(
            f"teams:{topic_id}", "team.message.deleted",
            {"messageId": message_id},
        )
    return {"ok": True}


@router.post("/messages/{message_id}/reactions")
async def toggle_reaction(message_id: str, body: ReactionIn, user: dict = Me) -> dict:
    db = await get_db()
    # The reaction target must live in the caller's workspace — otherwise a
    # user could react to messages in another org and fan a broker event there.
    cur = await db.execute(
        "SELECT 1 FROM teams_messages WHERE id = ? AND workspace_id = ?",
        (message_id, user["workspace_id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "message not found")
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
    db = await get_db()
    await _assert_conversation_in_workspace(db, cid, user["workspace_id"], user["id"])
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
    is_channel = await _assert_conversation_in_workspace(db, cid, user["workspace_id"], user["id"])
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
