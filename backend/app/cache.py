"""Redis-backed hot-read cache (spec §9 "Cache layer").



Rationale
---------
The /auth/me → /workspace → /me/unread-counts → /conversations/:id/messages
chain fires on every page navigation. Each one is cheap in isolation but lands
as a burst of 4-6 SELECTs on the primary. At single-worker scale this is fine;
past the first replica it becomes the dominant read hotspot.

This module gives the routers a thin async `get/set/invalidate` surface with a
graceful fallback: if `REDIS_URL` isn't configured or the Redis round-trip
fails, the cache operates in no-op mode and the request falls through to the
DB. Invalidation is NOTIFY-driven — routers that mutate a given key's source
rows call `invalidate()` in the same transaction so a stale read window is
bounded at one in-flight request.

Contract
--------
- Keys are plain strings. The convention mirrors the spec:
    me:<user_id>              — /auth/me payload
    unread:<user_id>          — /me/unread-counts
    ticket:<ticket_id>        — /tickets/:id detail
    conv:<conv_id>:head       — /conversations/:id's latest ~50 messages
- Values are JSON-serializable. We JSON-encode on set and decode on get so
  the caller never deals with bytes.
- TTLs default to 60 s. Pass `ttl=` for shorter windows (e.g. 15 s for
  unread counters).
- Non-fatal on Redis outage: the module flips to `_degraded=True` for 30 s
  on any exception so we don't hammer an unhealthy Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

_redis: Any = None
_lock = asyncio.Lock()
_degraded_until = 0.0
DEGRADE_SECONDS = 30.0
DEFAULT_TTL = 60


async def _client() -> Any | None:
    """Return a shared async Redis client, or None when disabled."""
    global _redis, _degraded_until
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    if _redis is not None:
        return _redis
    if time.monotonic() < _degraded_until:
        return None
    async with _lock:
        if _redis is not None:
            return _redis
        try:
            import redis.asyncio as aioredis  # type: ignore
            _redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
            # ping so we surface connection issues up front instead of on the
            # first real read.
            await _redis.ping()
            log.info("cache: redis online at %s", url)
        except Exception as e:
            log.warning("cache: redis unavailable (%s) — degrading", e)
            _degraded_until = time.monotonic() + DEGRADE_SECONDS
            _redis = None
            return None
        return _redis


def _mark_degraded(err: Exception) -> None:
    global _degraded_until
    _degraded_until = time.monotonic() + DEGRADE_SECONDS
    log.warning("cache: degraded for %ds (%s)", DEGRADE_SECONDS, err)


async def get(key: str) -> Any | None:
    """Return the cached value for `key`, or None if missing / unavailable."""
    cli = await _client()
    if cli is None:
        return None
    try:
        raw = await cli.get(f"wx:{key}")
    except Exception as e:
        _mark_degraded(e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def set(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    """Best-effort cache write. Silently drops on Redis error."""
    cli = await _client()
    if cli is None:
        return
    try:
        await cli.set(f"wx:{key}", json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        _mark_degraded(e)


async def invalidate(*keys: str) -> None:
    """Drop one or more keys from the cache. Always safe to call — invalid
    keys are no-ops, Redis outage is a no-op."""
    if not keys:
        return
    cli = await _client()
    if cli is None:
        return
    try:
        await cli.delete(*[f"wx:{k}" for k in keys])
    except Exception as e:
        _mark_degraded(e)


async def get_or_load(key: str, loader, ttl: int = DEFAULT_TTL) -> Any:
    """Classic read-through: return the cached value, or call `loader()` and
    cache its result. `loader` may be a coroutine or a plain callable.
    """
    cached = await get(key)
    if cached is not None:
        return cached
    if asyncio.iscoroutinefunction(loader):
        value = await loader()
    else:
        value = loader()
    if value is not None:
        await set(key, value, ttl=ttl)
    return value


# ---------- well-known key helpers -----------------------------------------

def k_me(user_id: str) -> str:
    return f"me:{user_id}"


def k_unread(user_id: str) -> str:
    return f"unread:{user_id}"


def k_ticket(ticket_id: str | int) -> str:
    return f"ticket:{ticket_id}"


def k_conv_head(conv_id: str) -> str:
    return f"conv:{conv_id}:head"
