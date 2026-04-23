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
    """Debit one token. Returns True when the caller may proceed, False when
    rate-limited. `scope` is the caller-chosen partition key — typically a
    workspace id, shop domain, or phone_number_id.

    Best-effort: if the bucket table doesn't exist yet (schema lag), allow
    the request so webhook wiring isn't broken by an old DB.
    """
    key = f"{provider}:{scope}"
    cap, refill = _cfg(provider)
    now = datetime.now(timezone.utc)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT tokens, capacity, refill_per_sec, last_refill_at FROM rate_limit_buckets WHERE bucket_key = ?",
            (key,),
        )
        row = await cur.fetchone()
    except Exception:
        return True

    if row is None:
        # First hit — insert with capacity - 1 tokens and allow.
        try:
            await db.execute(
                """
                INSERT INTO rate_limit_buckets (bucket_key, tokens, capacity, refill_per_sec, last_refill_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (bucket_key) DO UPDATE
                   SET tokens = GREATEST(rate_limit_buckets.tokens - 1, 0)
                """,
                (key, cap - 1, cap, refill, now.isoformat()),
            )
            await db.commit()
        except Exception:
            pass
        return True

    r = dict(row)
    tokens = float(r.get("tokens") or 0)
    capacity = float(r.get("capacity") or cap)
    rps = float(r.get("refill_per_sec") or refill)
    last_ts = r.get("last_refill_at")
    if isinstance(last_ts, str):
        try:
            last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        except Exception:
            last = now
    elif isinstance(last_ts, datetime):
        last = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
    else:
        last = now

    elapsed = max(0.0, (now - last).total_seconds())
    tokens = min(capacity, tokens + elapsed * rps)
    if tokens < 1.0:
        # Persist the refilled counter so repeated denials don't starve
        # the bucket from moving forward.
        try:
            await db.execute(
                "UPDATE rate_limit_buckets SET tokens = ?, last_refill_at = ? WHERE bucket_key = ?",
                (tokens, now.isoformat(), key),
            )
            await db.commit()
        except Exception:
            pass
        return False

    tokens -= 1.0
    try:
        await db.execute(
            "UPDATE rate_limit_buckets SET tokens = ?, last_refill_at = ? WHERE bucket_key = ?",
            (tokens, now.isoformat(), key),
        )
        await db.commit()
    except Exception:
        pass
    return True
