"""FastAPI entrypoint.

Run: `uvicorn app.main:app --reload --port 8000`
"""
from __future__ import annotations

import contextlib

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS
from .db import get_db, close_db
from .s3 import ensure_bucket
from .tracing import TraceMiddleware
from .routers import (
    agents,
    analytics,
    auth,
    billing,
    channels,
    conversations,
    customers,
    langflow,
    langflow_proxy,
    me,
    notifications,
    oauth,
    platforms,
    search,
    teams,
    tickets,
    uploads,
    webhooks,
    widget,
)
from .ws import ws_endpoint


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import logging
    import os
    # Connect to Postgres + apply schema + seed if empty (see app/db.py).
    await get_db()
    # Make sure the MinIO bucket exists. Non-fatal if MinIO isn't reachable.
    ensure_bucket()

    # Dev convenience: embed the background worker loops in the API process
    # so operators don't need a second terminal. Production runs the dedicated
    # `python -m app.worker` container and should set EMBED_WORKER=0 so the
    # loops don't duplicate. Defaults to ON.
    embed = os.environ.get("EMBED_WORKER", "1") != "0"
    worker_tasks: list[asyncio.Task] = []
    if embed:
        from . import worker as w
        stop = asyncio.Event()
        loops = [
            (w._outbox_tick,              0.5,  "outbox"),
            (w._sla_tick,                30.0,  "sla"),
            (w._auto_close_tick,         60.0,  "auto_close"),
            (w._gmail_poller_tick,       30.0,  "gmail_poller"),
            (w._health_prober_tick,      60.0,  "health_prober"),
            (w._counter_refresh_tick,    30.0,  "counters"),
            (w._embedding_indexer_tick,  60.0,  "embedding_indexer"),
            (w._oauth_state_cleanup_tick, 600,  "oauth_cleanup"),
            (w._watch_renewer_tick,     3600.0, "watch_renewer"),
            (w._langflow_publish_poll_tick, 30.0, "langflow_poll"),
        ]
        for fn, interval, name in loops:
            worker_tasks.append(asyncio.create_task(
                w._run_loop(fn, interval, stop, name)
            ))
        logging.getLogger("app.main").info(
            "embedded worker loops started: %s", [n for _, _, n in loops]
        )
        app.state._worker_stop = stop
        app.state._worker_tasks = worker_tasks

    try:
        yield
    finally:
        if worker_tasks:
            app.state._worker_stop.set()
            for t in worker_tasks:
                t.cancel()
            for t in worker_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await close_db()


app = FastAPI(title="WorkspaceX API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Stamps every request with a stable `trace_id` and exposes it via the
# `current_trace_id()` contextvar — used by api_logs and makes webhook →
# db-write → WS-push chains correlatable.
app.add_middleware(TraceMiddleware)


# Auth + session
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(me.router)

# Core app surface
app.include_router(conversations.router)
app.include_router(tickets.router)
app.include_router(customers.router)
app.include_router(teams.router)
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(platforms.router)
app.include_router(analytics.router)
app.include_router(analytics.csat_router)
app.include_router(agents.router)
app.include_router(agents.draft_router)
app.include_router(channels.router)
app.include_router(uploads.router)
app.include_router(billing.router)
# Inbound channel webhooks (WA / Gmail / Outlook / IG / Shopify / Slack / Voice /
# Stripe). Unauthenticated; per-provider HMAC/OIDC validation inside.
app.include_router(webhooks.router)
# Langflow: admin SSO launch + publish webhook (spec §3.11.A–E). The launch
# route mounts under `/flow`; the webhook sits under `/webhooks/langflow/*`
# so it lines up with the other provider webhooks.
app.include_router(langflow.launch_router)
app.include_router(langflow.webhook_router)
# Reverse-proxy Langflow's UI + API under `/lf/*`. Keeps everything on the
# backend origin (shared cookies) and injects a brand-strip stylesheet into
# HTML responses. Needs `LANGFLOW_BASE_URL_PATH=/lf` on the Langflow
# container so its own frontend emits matching relative URLs.
app.include_router(langflow_proxy.proxy_router)
# Widget router includes HTTP (`/widget/bootstrap`, `/widget/messages`,
# `/widget.js`) and WebSocket (`/widget/ws`) routes — `include_router` mounts
# all three.
app.include_router(widget.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def ws(ws: WebSocket) -> None:
    await ws_endpoint(ws)
