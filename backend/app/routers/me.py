from __future__ import annotations

from fastapi import APIRouter

from ..db import get_db
from ..deps import Me

router = APIRouter(tags=["me"])


@router.get("/me")
async def get_me(user: dict = Me) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "provider": user["provider"],
        "workspaceId": user["workspace_id"],
    }


@router.patch("/me")
async def patch_me(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    fields = {k: v for k, v in body.items() if k in {"name"}}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user["id"])
        )
        await db.commit()
    return await get_me(user)


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


@router.get("/workspace/members")
async def get_workspace_members(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, email, role, status, avatar_url FROM users WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
