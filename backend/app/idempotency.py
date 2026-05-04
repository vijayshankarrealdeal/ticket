"""Idempotency helper — stores the first response for a (scope, key) pair and
replays it on repeat POSTs so webhook retries / flaky clients don't create
duplicates.

Two APIs:

- `check_idempotency` / `save_idempotency` (legacy) — separate read + write.
  Two concurrent requests with the same key both miss the cache, both run the
  expensive work, and only the loser's INSERT no-ops. Use this only for ops
  that are themselves idempotent against double-execution.

- `claim_idempotency` (preferred) — atomic claim: `INSERT … ON CONFLICT DO
  NOTHING RETURNING id` succeeds for exactly one caller. The loser reads the
  pending row and waits for the winner's response to land via
  `complete_idempotency`. Eliminates the duplicate-work race entirely.

Usage of the preferred API:

    from fastapi import Header
    from .idempotency import claim_idempotency, complete_idempotency

    async def post(body, idempotency_key: str | None = Header(default=None), user=Me):
        claim = await claim_idempotency(user, "tickets.create", idempotency_key)
        if claim["status"] == "replay":
            return claim["response"]
        try:
            result = ...
        except Exception:
            await complete_idempotency(user, "tickets.create", idempotency_key, error=True)
            raise
        await complete_idempotency(user, "tickets.create", idempotency_key, response=result)
        return result

Key is opaque; clients pass the same string on retries. Missing key = no
idempotency protection (matches the HTTP semantics: idempotency is opt-in).
"""
from __future__ import annotations

import asyncio
from typing import Any

from .db import get_db, jl, js


# How long to wait for a concurrent winner to write its response before giving
# up and letting the loser run its own work (with the duplicate-execution risk
# this re-introduces). Set high enough to cover normal request latency.
_REPLAY_TIMEOUT_SECONDS = 10.0
_REPLAY_POLL_INTERVAL = 0.1


async def check_idempotency(user: dict, scope: str, key: str | None) -> Any | None:
    """Legacy read-only check. Prefer `claim_idempotency` for new code."""
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
    """Legacy write. Prefer `complete_idempotency` paired with `claim_idempotency`."""
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


async def claim_idempotency(user: dict, scope: str, key: str | None) -> dict:
    """Atomically reserve the (workspace, scope, key) slot for this caller.

    Returns one of:
    - `{"status": "fresh"}` — caller won the race, do the work, then call
      `complete_idempotency` with the response.
    - `{"status": "replay", "response": <stored_value>}` — another caller
      already finished; return their response unchanged.
    - `{"status": "no_key"}` — `key` was empty/None; caller should run
      without idempotency protection.

    The claim is atomic via `INSERT … ON CONFLICT DO NOTHING RETURNING`,
    which lets exactly one concurrent caller see a `RETURNING` row. Losers
    poll briefly for the winner's response to land before giving up.
    """
    if not key:
        return {"status": "no_key"}
    db = await get_db()
    # An empty `response` ('null' jsonb) is the sentinel for "claimed, not
    # yet completed". Once the winner finishes, they UPDATE the response.
    cur = await db.execute(
        """
        INSERT INTO idempotency_keys (workspace_id, user_id, scope, key, response)
        VALUES (?,?,?,?, 'null'::jsonb)
        ON CONFLICT (workspace_id, scope, key) DO NOTHING
        RETURNING id
        """,
        (user["workspace_id"], user["id"], scope, key),
    )
    won = await cur.fetchone()
    await db.commit()
    if won is not None:
        return {"status": "fresh"}

    # Lost the race. Poll for the winner's response.
    deadline = asyncio.get_event_loop().time() + _REPLAY_TIMEOUT_SECONDS
    while True:
        cur = await db.execute(
            """
            SELECT response FROM idempotency_keys
             WHERE workspace_id = ? AND scope = ? AND key = ?
            """,
            (user["workspace_id"], scope, key),
        )
        row = await cur.fetchone()
        if row is not None:
            stored = jl(row[0], None)
            if stored is not None:
                return {"status": "replay", "response": stored}
        if asyncio.get_event_loop().time() >= deadline:
            # Winner crashed or is stuck. Best-effort: clear the sentinel so
            # this caller (or the next one) can claim afresh, and report
            # `fresh` so we don't block the API forever.
            await db.execute(
                """
                DELETE FROM idempotency_keys
                 WHERE workspace_id = ? AND scope = ? AND key = ?
                   AND response = 'null'::jsonb
                """,
                (user["workspace_id"], scope, key),
            )
            await db.commit()
            return {"status": "fresh"}
        await asyncio.sleep(_REPLAY_POLL_INTERVAL)


async def complete_idempotency(
    user: dict,
    scope: str,
    key: str | None,
    *,
    response: Any = None,
    error: bool = False,
) -> None:
    """Finalise a slot claimed via `claim_idempotency`.

    On success, store `response` so future replays return it. On error, drop
    the row entirely so a retry can re-do the work — caching a half-finished
    failure response is worse than not caching at all.
    """
    if not key:
        return
    db = await get_db()
    if error:
        await db.execute(
            """
            DELETE FROM idempotency_keys
             WHERE workspace_id = ? AND scope = ? AND key = ?
               AND response = 'null'::jsonb
            """,
            (user["workspace_id"], scope, key),
        )
    else:
        await db.execute(
            """
            UPDATE idempotency_keys
               SET response = ?
             WHERE workspace_id = ? AND scope = ? AND key = ?
            """,
            (js(response), user["workspace_id"], scope, key),
        )
    await db.commit()
