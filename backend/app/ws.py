"""Topic broker for WebSocket fan-out.

Topics used by the frontend (spec §8):
- inbox:<workspace_id>           – conversation list updates
- conv:<conversation_id>         – messages + typing for an open thread
- ticket:<ticket_id>              – ticket detail live updates
- notif:<user_id>                 – notifications for a user
- teams:<channel_or_dm_id>        – team chat messages
- presence:<workspace_id>         – presence changes
- agents:<workspace_id>           – Langflow publish notifications

Two backends share a common interface:

- **InProcessBroker** (default) — single-worker Uvicorn. Map `topic → set[ws]`,
  fan-out happens in the same process.
- **RedisBroker** (set `BROKER_KIND=redis` + `REDIS_URL`) — every pod `PSUBSCRIBE`s
  its own channel and re-publishes to its local websockets. Horizontal-ready
  per wire.md §'Scaling the broker'.

Same `subscribe / unsubscribe / publish` surface so nothing downstream
changes. Failures on the Redis side fall back to in-process delivery so a
broker outage never silences the app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect, status

log = logging.getLogger(__name__)


class InProcessBroker:
    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket, topics: list[str]) -> None:
        async with self._lock:
            for t in topics:
                self._subs[t].add(ws)

    async def unsubscribe(self, ws: WebSocket, topics: list[str] | None = None) -> None:
        async with self._lock:
            if topics is None:
                for subs in self._subs.values():
                    subs.discard(ws)
            else:
                for t in topics:
                    self._subs.get(t, set()).discard(ws)

    async def _deliver_local(self, topic: str, envelope: dict) -> None:
        dead: list[WebSocket] = []
        subs = list(self._subs.get(topic, ()))
        for ws in subs:
            try:
                await ws.send_text(json.dumps(envelope, default=str))
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    for subs in self._subs.values():
                        subs.discard(ws)

    async def publish(self, topic: str, type_: str, data: Any) -> None:
        envelope = {
            "type": type_,
            "topic": topic,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        await self._deliver_local(topic, envelope)


class RedisBroker(InProcessBroker):
    """Fans out publishes through Redis so every Uvicorn pod sees them.

    Each pod `PSUBSCRIBE`s the shared prefix on startup. `publish()` hops
    through `PUBLISH workspacex:<topic>`; the subscriber task in the pod
    translates that back into a local `_deliver_local` call so any WS
    connected to *this* pod receives the envelope.

    If the Redis client isn't installed or the server is unreachable, we
    degrade to in-process delivery only — the app keeps working, but pods
    stop seeing each other's events.
    """

    PREFIX = "workspacex:"

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self._redis = None  # set lazily by `_ensure`
        self._pubsub_task: asyncio.Task | None = None

    async def _ensure(self) -> None:
        if self._redis is not None:
            return
        try:
            import redis.asyncio as redis_lib  # type: ignore
        except ImportError:
            log.warning("redis package not installed — falling back to in-proc broker")
            return
        try:
            self._redis = redis_lib.from_url(self._url, decode_responses=True)
            await self._redis.ping()
        except Exception as e:
            log.warning("redis unavailable (%r) — falling back to in-proc broker", e)
            self._redis = None
            return
        # Kick off the pubsub listener once per process.
        if self._pubsub_task is None or self._pubsub_task.done():
            self._pubsub_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._redis is not None
        try:
            ps = self._redis.pubsub()
            await ps.psubscribe(f"{self.PREFIX}*")
            async for msg in ps.listen():
                if msg.get("type") != "pmessage":
                    continue
                channel = (msg.get("channel") or "").removeprefix(self.PREFIX)
                try:
                    envelope = json.loads(msg.get("data") or "{}")
                except Exception:
                    continue
                await self._deliver_local(channel, envelope)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("redis pubsub listener crashed — next publish will retry connect")
            self._redis = None

    async def publish(self, topic: str, type_: str, data: Any) -> None:
        envelope = {
            "type": type_,
            "topic": topic,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        await self._ensure()
        if self._redis is None:
            await self._deliver_local(topic, envelope)
            return
        try:
            await self._redis.publish(
                f"{self.PREFIX}{topic}",
                json.dumps(envelope, default=str),
            )
        except Exception as e:
            log.warning("redis publish failed (%r) — delivering locally only", e)
            await self._deliver_local(topic, envelope)


def _make_broker():
    kind = (os.environ.get("BROKER_KIND") or "memory").lower()
    url = os.environ.get("REDIS_URL")
    if kind == "redis" and url:
        return RedisBroker(url)
    return InProcessBroker()


broker = _make_broker()

# Backwards-compatibility alias — callers still reference `Broker`.
Broker = InProcessBroker


async def _authorize_topic(user: dict, topic: str) -> bool:
    """Verify the authenticated user is allowed to subscribe to `topic`.

    A user with a valid session would otherwise be able to subscribe to any
    `conv:<uuid>` / `ticket:<uuid>` / `inbox:<ws>` and stream another
    workspace's traffic. Each topic kind below resolves the underlying entity
    and confirms it belongs to the user's workspace (or, for `notif:`, that
    the id matches the user themselves).
    """
    if not topic or ":" not in topic:
        return False
    kind, _, key = topic.partition(":")
    if not key:
        return False
    user_ws = user.get("workspace_id")
    user_id = user.get("id")

    if kind in ("inbox", "presence", "agents"):
        return key == user_ws
    if kind == "notif":
        return key == user_id
    if kind in ("conv", "ticket", "teams"):
        from .db import get_db  # local to avoid cycles
        db = await get_db()
        if kind == "conv":
            cur = await db.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND workspace_id = ?",
                (key, user_ws),
            )
            return (await cur.fetchone()) is not None
        if kind == "ticket":
            cur = await db.execute(
                "SELECT 1 FROM tickets WHERE id = ? AND workspace_id = ?",
                (key, user_ws),
            )
            return (await cur.fetchone()) is not None
        # teams: channel (workspace-open) OR DM (membership-gated).
        cur = await db.execute(
            "SELECT 1 FROM teams_channels WHERE id = ? AND workspace_id = ?",
            (key, user_ws),
        )
        if await cur.fetchone():
            return True
        cur = await db.execute(
            """
            SELECT 1 FROM teams_dms
             WHERE id = ? AND workspace_id = ? AND member_ids @> ?::jsonb
            """,
            (key, user_ws, json.dumps([user_id])),
        )
        return (await cur.fetchone()) is not None
    # Unknown topic kinds are denied by default.
    return False


async def ws_endpoint(ws: WebSocket) -> None:
    """Client protocol:

    → {"action":"subscribe","topics":["inbox:ws_1","conv:abc"]}
    → {"action":"unsubscribe","topics":["conv:abc"]}
    → {"action":"ping"}

    ← envelope = {type, topic, at, data}
    ← {"type":"pong"}
    """
    from .routers.auth import session_user_from_token  # avoid circular

    token = ws.query_params.get("token")
    user = await session_user_from_token(token) if token else None
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    try:
        await ws.send_text(json.dumps({"type": "hello", "data": {"user_id": user["id"]}}))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            action = msg.get("action")
            if action == "subscribe":
                requested = [t for t in (msg.get("topics") or []) if isinstance(t, str)]
                allowed: list[str] = []
                denied: list[str] = []
                for t in requested:
                    try:
                        ok = await _authorize_topic(user, t)
                    except Exception:
                        ok = False
                    (allowed if ok else denied).append(t)
                if allowed:
                    await broker.subscribe(ws, allowed)
                await ws.send_text(json.dumps({
                    "type": "subscribed",
                    "topics": allowed,
                    "denied": denied,
                }))
            elif action == "unsubscribe":
                topics = msg.get("topics")
                await broker.unsubscribe(ws, topics if isinstance(topics, list) else None)
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await broker.unsubscribe(ws, None)
