"""Idempotency helper — stores the first response for a (scope, key) pair and
replays it on repeat POSTs so webhook retries / flaky clients don't create
duplicates.

Usage:

    from fastapi import Header
    from .idempotency import check_idempotency, save_idempotency

    async def post(body, idempotency_key: str | None = Header(default=None), user=Me):
        cached = await check_idempotency(user, "tickets.create", idempotency_key)
        if cached is not None:
            return cached
        ...
        await save_idempotency(user, "tickets.create", idempotency_key, result)
        return result

Key is opaque; clients pass the same string on retries. Missing key = no
idempotency protection (matches the HTTP semantics: idempotency is opt-in).
"""
from __future__ import annotations

from typing import Any

from .db import get_db, jl, js


async def check_idempotency(user: dict, scope: str, key: str | None) -> Any | None:
    if not key:
        return None
    db = await get_db()
    cur = await db.execute(
        """
        SELECT response FROM idempotency_keys
         WHERE workspace_id = ? AND scope = ? AND key = ?
        """,
        (user["workspace_id"], scope, key),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return jl(row[0], None)


async def save_idempotency(user: dict, scope: str, key: str | None, response: Any) -> None:
    if not key:
        return
    db = await get_db()
    await db.execute(
        """
        INSERT INTO idempotency_keys (workspace_id, user_id, scope, key, response)
        VALUES (?,?,?,?,?)
        ON CONFLICT (workspace_id, scope, key) DO NOTHING
        """,
        (user["workspace_id"], user["id"], scope, key, js(response)),
    )
    await db.commit()
