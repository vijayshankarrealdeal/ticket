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
from .routers import (
    agents,
    analytics,
    auth,
    channels,
    conversations,
    customers,
    me,
    notifications,
    platforms,
    search,
    teams,
    tickets,
    uploads,
)
from .ws import ws_endpoint


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to Postgres + apply schema + seed if empty (see app/db.py).
    await get_db()
    # Make sure the MinIO bucket exists. Non-fatal if MinIO isn't reachable.
    ensure_bucket()
    try:
        yield
    finally:
        await close_db()


app = FastAPI(title="WorkspaceX API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Auth + session
app.include_router(auth.router)
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


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.websocket("/ws")
async def ws(ws: WebSocket) -> None:
    await ws_endpoint(ws)
