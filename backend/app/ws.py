"""In-memory topic broker for WebSocket fan-out.

Topics used by the frontend (spec §8):
- inbox:<workspace_id>           – conversation list updates
- conv:<conversation_id>         – messages + typing for an open thread
- ticket:<ticket_id>              – ticket detail live updates
- notif:<user_id>                 – notifications for a user
- teams:<channel_or_dm_id>        – team chat messages
- presence:<workspace_id>         – presence changes

For prod with multiple workers: replace Broker with a Redis Pub/Sub backed impl
(same interface). See wire.md §'Scaling the broker'.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect, status


class Broker:
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

    async def publish(self, topic: str, type_: str, data: Any) -> None:
        envelope = {
            "type": type_,
            "topic": topic,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
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


broker = Broker()


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
                topics = [t for t in (msg.get("topics") or []) if isinstance(t, str)]
                await broker.subscribe(ws, topics)
                await ws.send_text(json.dumps({"type": "subscribed", "topics": topics}))
            elif action == "unsubscribe":
                topics = msg.get("topics")
                await broker.unsubscribe(ws, topics if isinstance(topics, list) else None)
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await broker.unsubscribe(ws, None)
