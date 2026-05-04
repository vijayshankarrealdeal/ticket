"""Token-bucket rate limiter for inbound webhooks (spec §9).

Every inbound webhook handler in `routers/webhooks.py` debits one token from
a per-(provider, workspace) bucket before doing the expensive ingest work. A
hostile provider flooding us with events still racks up rejected POSTs but
doesn't push into the DB or stream the orchestrator.

State lives in the `rate_limit_buckets` table so concurrent Uvicorn workers
share a single bucket instead of each getting an in-process copy. Missing
rows behave like a full bucket — first hit inserts with `capacity` tokens,
immediately debits one, and returns allowed.

Defaults: 60 tokens, 1 refill/sec (so 60 rps burst, 1 rps sustained). Overrides
come from env vars `RL_<provider>_capacity` / `RL_<provider>_refill`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .db import get_db


def _cfg(provider: str) -> tuple[float, float]:
    """Per-provider (capacity, refill_per_sec). Env-overridable."""
    cap = float(os.environ.get(f"RL_{provider.upper()}_CAPACITY", "60"))
    ref = float(os.environ.get(f"RL_{provider.upper()}_REFILL", "1"))
    return cap, ref


async def allow(provider: str, scope: str) -> bool:
    """Debit one token atomically. Returns True when the caller may proceed,
    False when rate-limited. `scope` is the caller-chosen partition key —
    typically a workspace id, shop domain, or phone_number_id.

    Implementation: take a row-level lock with `SELECT … FOR UPDATE` inside a
    transaction so the read-refill-write sequence is atomic. Two concurrent
    requests on the same bucket are serialized on that lock; without it,
    they can both observe `tokens >= 1`, both decrement, and the bucket
    silently goes negative (allowing 2× the configured burst).

    Best-effort: if the bucket table doesn't exist yet (schema lag), allow
    the request so webhook wiring isn't broken by an old DB.
    """
    key = f"{provider}:{scope}"
    cap, refill = _cfg(provider)
    db = await get_db()
    try:
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT tokens, capacity, refill_per_sec, last_refill_at
                      FROM rate_limit_buckets
                     WHERE bucket_key = $1
                       FOR UPDATE
                    """,
                    key,
                )
                if row is None:
                    # First hit. Insert with capacity - 1 (debiting this
                    # request as part of the initial state). Concurrent
                    # callers race here on the unique constraint; the loser
                    # falls into the UPDATE branch on the next call.
                    await conn.execute(
                        """
                        INSERT INTO rate_limit_buckets
                              (bucket_key, tokens, capacity, refill_per_sec, last_refill_at)
                        VALUES ($1, $2, $3, $4, now())
                        ON CONFLICT (bucket_key) DO NOTHING
                        """,
                        key, cap - 1, cap, refill,
                    )
                    return True

                now = datetime.now(timezone.utc)
                last_ts = row["last_refill_at"]
                if isinstance(last_ts, str):
                    try:
                        last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    except Exception:
                        last = now
                elif isinstance(last_ts, datetime):
                    last = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
                else:
                    last = now
                capacity = float(row["capacity"] or cap)
                rps = float(row["refill_per_sec"] or refill)
                tokens = float(row["tokens"] or 0)
                elapsed = max(0.0, (now - last).total_seconds())
                tokens = min(capacity, tokens + elapsed * rps)

                allowed = tokens >= 1.0
                if allowed:
                    tokens -= 1.0
                await conn.execute(
                    """
                    UPDATE rate_limit_buckets
                       SET tokens = $1, last_refill_at = now()
                     WHERE bucket_key = $2
                    """,
                    tokens, key,
                )
                return allowed
    except Exception:
        return True
