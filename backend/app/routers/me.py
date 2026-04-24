from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .. import cache
from ..db import get_db, jl, js
from ..deps import AdminOnly, Me
from ..ws import broker
from .notifications import create_notification

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
    # Hot-read cache: TopBar badge fires on every navigation, so we serve
    # this out of Redis with a 15 s TTL. Notification mutations invalidate
    # the key; cold misses fall through to the real SQL below.
    cached = await cache.get(cache.k_unread(user["id"]))
    if cached is not None:
        return cached
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(SUM(unread_count),0) FROM conversations WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    inbox = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND unread = true",
        (user["id"],),
    )
    notifs = (await cur.fetchone())[0] or 0
    # Teams unread: aggregate channel + DM messages since the caller's last-
    # read cursor. Mirrors the math in /teams/unread so the TopBar badge and
    # the Teams sidebar stay consistent.
    cur = await db.execute(
        """
        SELECT COUNT(*)
          FROM teams_messages m
          LEFT JOIN teams_channels ch ON ch.id = m.channel_id
          LEFT JOIN teams_dms d       ON d.id = m.dm_id
          LEFT JOIN teams_read_cursors rc_ch ON rc_ch.channel_id = m.channel_id AND rc_ch.user_id = ?
          LEFT JOIN teams_read_cursors rc_dm ON rc_dm.dm_id = m.dm_id         AND rc_dm.user_id = ?
         WHERE m.workspace_id = ? AND m.user_id <> ?
           AND (
             (m.channel_id IS NOT NULL AND m.at > COALESCE(rc_ch.last_read_at, '1970-01-01'::timestamptz))
             OR
             (m.dm_id IS NOT NULL AND m.at > COALESCE(rc_dm.last_read_at, '1970-01-01'::timestamptz))
           )
        """,
        (user["id"], user["id"], user["workspace_id"], user["id"]),
    )
    teams_unread = int((await cur.fetchone())[0] or 0)
    payload = {"inbox": int(inbox), "notifications": int(notifs), "teamsUnread": teams_unread}
    await cache.set(cache.k_unread(user["id"]), payload, ttl=15)
    return payload


@router.get("/workspace/counters")
async def workspace_counters(user: dict = Me) -> dict:
    """Pre-aggregated badge counts from workspace_counters (refreshed by the
    counter worker every 30s). Falls back to zeros on a cold install so the
    sidebar still renders while the first refresh is pending."""
    db = await get_db()
    cur = await db.execute(
        "SELECT kind, value FROM workspace_counters WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = {r["kind"]: int(r["value"]) for r in rows}
    return {
        "ticketsOpen": out.get("tickets_open", 0),
        "conversationsUnread": out.get("conversations_unread", 0),
        "notificationsUnread": out.get("notifications_unread", 0),
    }


@router.get("/workspace/broadcasts")
async def list_broadcasts(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT b.*, u.name AS author_name
          FROM broadcasts b LEFT JOIN users u ON u.id = b.author_id
         WHERE b.workspace_id = ?
         ORDER BY b.created_at DESC LIMIT 50
        """,
        (user["workspace_id"],),
    )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "body": r.get("body"),
            "link": r.get("link"),
            "author": r.get("author_name"),
            "createdAt": r.get("created_at"),
        }
        for r in [dict(x) for x in await cur.fetchall()]
    ]


@router.post("/workspace/broadcast")
async def post_broadcast(body: dict, user: dict = AdminOnly) -> dict:
    """Admin-only announcement. Persists a broadcasts row + fans one
    notification out to every workspace member (spec §3.7 broadcast
    generator)."""
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(400, "title required")
    text = (body or {}).get("body")
    link = (body or {}).get("link")
    db = await get_db()
    bid = str(uuid4())
    await db.execute(
        "INSERT INTO broadcasts (id, workspace_id, author_id, title, body, link) VALUES (?,?,?,?,?,?)",
        (bid, user["workspace_id"], user["id"], title, text, link),
    )
    await db.commit()
    cur = await db.execute(
        "SELECT id, email, notification_prefs FROM users WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    recipients = [dict(r) for r in await cur.fetchall()]
    delivered = 0
    for r in recipients:
        if r["id"] == user["id"]:
            continue
        delivered += 1
        await create_notification(
            r["id"], "broadcast",
            title=title,
            body=text,
            link=link,
        )
        # Honour per-user email opt-in. Default prefs (from schema) include
        # `"email": true` so members get email until they explicitly opt out.
        prefs = jl(r.get("notification_prefs"), {}) or {}
        if prefs.get("email") and r.get("email"):
            await db.execute(
                """
                INSERT INTO outbox (workspace_id, kind, payload)
                VALUES (?, 'email', ?)
                """,
                (user["workspace_id"], js({
                    "workspace_id": user["workspace_id"],
                    "to": r["email"],
                    "subject": f"[Broadcast] {title}",
                    "body": (text or "") + (f"\n\n{link}" if link else ""),
                    "kind": "broadcast",
                })),
            )
    await db.commit()
    return {"id": bid, "delivered": delivered}


# ---------- Delivery log (admin visibility into outbox sends) ----------

@router.get("/workspace/delivery-log")
async def list_delivery_log(limit: int = 100, user: dict = AdminOnly) -> list[dict]:
    """Recent outbound deliveries (email / webhook / channel sends) for this
    workspace. Powers the Settings → Notifications → "Recent deliveries"
    panel so admins can confirm a broadcast / password-reset went through."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, kind, target, subject, status, error, at
          FROM delivery_log
         WHERE workspace_id = ?
         ORDER BY at DESC LIMIT ?
        """,
        (user["workspace_id"], int(min(max(limit, 1), 500))),
    )
    return [
        {"id": r["id"], "kind": r["kind"], "target": r["target"],
         "subject": r.get("subject"), "status": r["status"],
         "error": r.get("error"), "at": r["at"]}
        for r in [dict(x) for x in await cur.fetchall()]
    ]


@router.post("/workspace/members")
async def invite_member(body: dict, user: dict = AdminOnly) -> dict:
    """Invite someone into the workspace. Creates the user row in `invited`
    status, issues a password-reset token so the invitee can set their
    password via the /auth/reset-password flow, and fans a teammate_joined
    notification out to every existing member (spec §3.7)."""
    from passlib.hash import argon2
    email = (body or {}).get("email", "").strip().lower()
    name = (body or {}).get("name", email.split("@")[0] if email else "Teammate")
    role = (body or {}).get("role", "agent")
    if role not in ("viewer", "agent", "admin"):
        raise HTTPException(400, "invalid role")
    if not email or "@" not in email:
        raise HTTPException(400, "valid email required")
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM users WHERE workspace_id = ? AND email = ?",
        (user["workspace_id"], email),
    )
    if await cur.fetchone():
        raise HTTPException(409, "user already exists in workspace")

    uid = str(uuid4())
    temp = secrets.token_urlsafe(18)
    await db.execute(
        """
        INSERT INTO users (id, workspace_id, name, email, password_hash, provider, role, status)
        VALUES (?,?,?,?,?, 'password', ?, 'invited')
        """,
        (uid, user["workspace_id"], name, email, argon2.hash(temp), role),
    )
    # Issue a reset token so the invitee can pick their own password.
    reset_raw = secrets.token_urlsafe(24)
    reset_hash = hashlib.sha256(reset_raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, now() + interval '7 days')",
        (uid, reset_hash),
    )
    await db.commit()

    cur = await db.execute(
        "SELECT id FROM users WHERE workspace_id = ? AND id <> ?",
        (user["workspace_id"], uid),
    )
    for (existing_id,) in await cur.fetchall():
        await create_notification(
            existing_id, "teammate_joined",
            title=f"{name} joined the workspace",
            body=f"Role: {role}",
            link="/settings?tab=members",
        )
    return {
        "id": uid, "name": name, "email": email, "role": role, "status": "invited",
        "resetToken": reset_raw,
    }


@router.delete("/me/oauth/{provider}")
async def unlink_oauth(provider: str, user: dict = Me) -> dict:
    """Unlink an OAuth identity. Blocked if the user has no password hash and
    no other linked provider — otherwise they'd lock themselves out."""
    if provider not in ("google", "outlook"):
        raise HTTPException(400, "unknown provider")
    db = await get_db()
    cur = await db.execute(
        "SELECT password_hash, provider FROM users WHERE id = ?", (user["id"],)
    )
    row = dict(await cur.fetchone() or {})
    if not row.get("password_hash") and row.get("provider") == provider:
        raise HTTPException(
            400,
            "set a password via POST /me/password before unlinking your only sign-in provider",
        )
    await db.execute(
        "UPDATE users SET oauth_sub = NULL, provider = 'password' WHERE id = ? AND provider = ?",
        (user["id"], provider),
    )
    await db.commit()
    return {"ok": True, "provider": provider}


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


# ---------- SLA policies (spec §3.4, §11) ----------

def _sla_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "team": r.get("team"),
        "task": r.get("task"),
        "priority": r.get("priority"),
        "hours": float(r["hours"]),
        "createdAt": r.get("created_at"),
    }


@router.get("/workspace/sla-policies")
async def list_sla_policies(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, team, task, priority, hours, created_at FROM sla_policies
         WHERE workspace_id = ?
         ORDER BY (team IS NOT NULL)::int + (task IS NOT NULL)::int + (priority IS NOT NULL)::int DESC,
                  created_at DESC
        """,
        (user["workspace_id"],),
    )
    return [_sla_row(dict(r)) for r in await cur.fetchall()]


@router.post("/workspace/sla-policies")
async def create_sla_policy(body: dict, user: dict = AdminOnly) -> dict:
    hours = body.get("hours")
    if hours is None or float(hours) <= 0:
        raise HTTPException(400, "hours must be > 0")
    db = await get_db()
    pid = str(uuid4())
    team = (body.get("team") or "").strip() or None
    task = (body.get("task") or "").strip() or None
    priority = (body.get("priority") or "").strip() or None
    try:
        await db.execute(
            """
            INSERT INTO sla_policies (id, workspace_id, team, task, priority, hours)
            VALUES (?,?,?,?,?,?)
            """,
            (pid, user["workspace_id"], team, task, priority, float(hours)),
        )
        await db.commit()
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, "policy already exists for this team/task/priority")
        raise
    cur = await db.execute(
        "SELECT id, team, task, priority, hours, created_at FROM sla_policies WHERE id = ?",
        (pid,),
    )
    return _sla_row(dict(await cur.fetchone()))


@router.delete("/workspace/sla-policies/{pid}")
async def delete_sla_policy(pid: str, user: dict = AdminOnly) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM sla_policies WHERE id = ? AND workspace_id = ?",
        (pid, user["workspace_id"]),
    )
    await db.commit()
    return {"ok": True}


# ---------- 2FA TOTP (spec §3.1) ----------

def _b32_secret() -> str:
    """20-byte random secret, base32-encoded (standard TOTP)."""
    import base64
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    """RFC-6238 TOTP verify, 30 s step, ±`window` steps for clock skew."""
    import base64
    import hmac
    import struct
    import time
    code = (code or "").replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    try:
        key = base64.b32decode(secret_b32 + pad, casefold=True)
    except Exception:
        return False
    now_step = int(time.time() // 30)
    for offset in range(-window, window + 1):
        msg = struct.pack(">Q", now_step + offset)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = h[-1] & 0x0F
        bin_code = ((h[o] & 0x7F) << 24) | ((h[o + 1] & 0xFF) << 16) | ((h[o + 2] & 0xFF) << 8) | (h[o + 3] & 0xFF)
        otp = str(bin_code % 1_000_000).zfill(6)
        if hmac.compare_digest(otp, code):
            return True
    return False


@router.get("/me/2fa")
async def get_2fa(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT totp_enabled FROM users WHERE id = ?", (user["id"],)
    )
    r = dict(await cur.fetchone() or {})
    return {"enabled": bool(r.get("totp_enabled"))}


@router.post("/me/2fa/setup")
async def setup_2fa(user: dict = Me) -> dict:
    """Mint a fresh TOTP secret + provisioning URI. Stored under
    `users.totp_secret_pending`; only promoted to `totp_secret` after the user
    confirms a 6-digit code via /me/2fa/verify."""
    secret = _b32_secret()
    db = await get_db()
    await db.execute(
        "UPDATE users SET totp_secret_pending = ? WHERE id = ?",
        (secret, user["id"]),
    )
    await db.commit()
    issuer = "WorkspaceX"
    label = f"{issuer}:{user['email']}"
    uri = (f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&period=30&digits=6&algorithm=SHA1")
    return {"secret": secret, "otpauthUrl": uri}


@router.post("/me/2fa/verify")
async def verify_2fa(body: dict, user: dict = Me) -> dict:
    code = (body or {}).get("code", "")
    db = await get_db()
    cur = await db.execute(
        "SELECT totp_secret_pending FROM users WHERE id = ?", (user["id"],)
    )
    r = dict(await cur.fetchone() or {})
    pending = r.get("totp_secret_pending")
    if not pending:
        raise HTTPException(400, "no setup in progress; call /me/2fa/setup first")
    if not _verify_totp(pending, code):
        raise HTTPException(400, "invalid code")
    await db.execute(
        """UPDATE users SET totp_secret = ?, totp_secret_pending = NULL,
           totp_enabled = true WHERE id = ?""",
        (pending, user["id"]),
    )
    await db.commit()
    return {"enabled": True}


@router.post("/me/2fa/disable")
async def disable_2fa(body: dict, user: dict = Me) -> dict:
    """Disable 2FA. Requires the user to type a current code so a stolen session
    can't silently turn off the second factor."""
    db = await get_db()
    cur = await db.execute(
        "SELECT totp_secret, totp_enabled FROM users WHERE id = ?", (user["id"],)
    )
    r = dict(await cur.fetchone() or {})
    if not r.get("totp_enabled"):
        return {"enabled": False}
    code = (body or {}).get("code", "")
    if not _verify_totp(r.get("totp_secret") or "", code):
        raise HTTPException(400, "invalid code")
    await db.execute(
        "UPDATE users SET totp_secret = NULL, totp_enabled = false WHERE id = ?",
        (user["id"],),
    )
    await db.commit()
    return {"enabled": False}
