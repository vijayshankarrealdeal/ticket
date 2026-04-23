from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException

from ..db import get_db, jl, js
from ..deps import Me

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT avatar_url, notification_prefs FROM users WHERE id = ?", (user["id"],)
    )
    row = dict(await cur.fetchone() or {})
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "provider": user["provider"],
        "workspaceId": user["workspace_id"],
        "avatar_url": row.get("avatar_url"),
        "notificationPrefs": jl(row.get("notification_prefs"), {"email": True, "browser": True, "digest": "daily"}),
    }


@router.patch("/me")
async def patch_me(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    fields: dict = {}
    for k in ("name", "avatar_url"):
        if k in body:
            fields[k] = body[k]
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user["id"])
        )
        await db.commit()
    return await get_me(user)


@router.get("/me/notification-prefs")
async def get_notif_prefs(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute("SELECT notification_prefs FROM users WHERE id = ?", (user["id"],))
    row = await cur.fetchone()
    return jl(row[0] if row else None, {"email": True, "browser": True, "digest": "daily"})


@router.patch("/me/notification-prefs")
async def patch_notif_prefs(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute("SELECT notification_prefs FROM users WHERE id = ?", (user["id"],))
    row = await cur.fetchone()
    current = jl(row[0] if row else None, {})
    current.update({k: body[k] for k in body if k in {"email", "browser", "digest"}})
    await db.execute(
        "UPDATE users SET notification_prefs = ? WHERE id = ?",
        (js(current), user["id"]),
    )
    await db.commit()
    return current


@router.post("/me/password")
async def change_password(body: dict, user: dict = Me) -> dict:
    from passlib.hash import argon2
    current = body.get("current") or ""
    new = body.get("new") or ""
    if len(new) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    db = await get_db()
    cur = await db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
    row = await cur.fetchone()
    stored = row[0] if row else None
    if stored and not argon2.verify(current, stored):
        raise HTTPException(400, "current password incorrect")
    new_hash = argon2.hash(new)
    await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
    await db.commit()
    return {"ok": True}


@router.get("/me/unread-counts")
async def unread_counts(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(SUM(unread_count),0) FROM conversations WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    inbox = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND unread = 1",
        (user["id"],),
    )
    notifs = (await cur.fetchone())[0] or 0
    return {"inbox": int(inbox), "notifications": int(notifs), "teamsUnread": 0}


@router.get("/workspace")
async def get_workspace(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute("SELECT * FROM workspaces WHERE id = ?", (user["workspace_id"],))
    row = await cur.fetchone()
    return dict(row) if row else {}


@router.patch("/workspace")
async def patch_workspace(body: dict, user: dict = Me) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "admin only")
    db = await get_db()
    fields: dict = {}
    for k in ("name", "timezone", "default_lang", "plan", "ai_prompt_append", "ai_force_default"):
        if k in body:
            fields[k] = body[k]
    if "ai_allowed_tools" in body:
        fields["ai_allowed_tools"] = js(body["ai_allowed_tools"])
    if "intent_rules" in body:
        fields["intent_rules"] = js(body["intent_rules"])
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE workspaces SET {sets} WHERE id = ?",
            (*fields.values(), user["workspace_id"]),
        )
        await db.commit()
    return await get_workspace(user)


@router.get("/workspace/members")
async def get_workspace_members(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, email, role, status, avatar_url FROM users WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
