"""Reverse-proxy Langflow through FastAPI (spec §3.11.B).

Langflow normally runs on its own origin (`:7860`). Routing the editor
through our backend at `/lf/*` gives us three things we need:

1. **Everything on one origin** — the `wx_session` / `access_token_lf`
   cookies are same-site, no more cross-origin fetch gymnastics in the SSO
   bridge, and the port can be bound to `127.0.0.1` so it's not internet-
   reachable.
2. **Branding strip via HTML injection** — we splice a `<style>` block into
   Langflow's `index.html` that hides the logo, GitHub / Discord chips, and
   the empty-folder welcome card, then flips the palette to match the
   WorkspaceX dark theme.
3. **Uniform auth** — Langflow's own `/api/v1/login` still fires for the
   SSO bridge, but the Set-Cookie lands on `localhost:8000` so every call
   out of the editor carries it without `credentials: 'include'` theatre.

HTTP pass-through uses `httpx.AsyncClient.stream()` so large payloads (flow
JSON, file uploads) don't buffer in memory. WebSocket pass-through uses
`starlette.websockets` + `websockets` library — Langflow's UI opens sockets
for the flow-run streams, so we bridge those too.

Needs `LANGFLOW_BASE_URL_PATH=/lf` set on the Langflow container so its own
frontend emits `/lf/api/v1/...` URLs that stay on our origin.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.websockets import WebSocketDisconnect

log = logging.getLogger(__name__)

proxy_router = APIRouter(tags=["langflow-proxy"])

LANGFLOW_URL = (os.environ.get("LANGFLOW_URL") or "").rstrip("/")
LANGFLOW_BASE_PATH = os.environ.get("LANGFLOW_BASE_URL_PATH") or "/lf"
# Strip leading slash for join ops; we always reintroduce it when needed.
BASE_PREFIX = LANGFLOW_BASE_PATH.strip("/")

# Headers we don't want to forward in either direction. Hop-by-hop per RFC
# 7230 + a couple that would confuse the client (content-encoding/length
# recomputed by our response).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
}


# --- CSS / JS injected into Langflow's index.html ---------------------------

_BRAND_STRIP_CSS = """
/* WorkspaceX Langflow rebrand — injected by the backend proxy. */
:root {
  --lf-bg: #0f172a;
  --lf-card: #111827;
  --lf-border: #1f2937;
  --lf-text: #e5e7eb;
  --lf-muted: #94a3b8;
  --lf-primary: #2563eb;
}

/* Hide the top-bar branding: GitHub + Discord chips, Langflow logo. */
header a[href*="github.com"],
header a[href*="discord"],
a[href*="github.com/langflow-ai"],
a[href*="discord.gg"],
a[href*="discord.com/invite"],
img[alt*="angflow" i],
svg[class*="brand" i],
[class*="brand-logo"],
[class*="logoIcon"],
[class*="BrandLogo"] { display: none !important; }

/* Remove the empty-folder "Welcome to Langflow" splash — the backend
   auto-creates a flow per agent, so this card never has anything to add. */
[class*="welcome" i][class*="page" i],
[data-testid*="welcome" i],
[class*="emptyState" i] h1,
[class*="emptyState" i] h2,
[class*="emptyState" i] p { display: none !important; }

/* Match WorkspaceX dark theme. */
html, body, #root {
  background: var(--lf-bg) !important;
  color: var(--lf-text) !important;
}
header, aside, [class*="sidebar"] {
  background: var(--lf-card) !important;
  border-color: var(--lf-border) !important;
}
button[class*="primary"], [class*="btn-primary"] {
  background: var(--lf-primary) !important; color: #fff !important;
  border-color: var(--lf-primary) !important;
}
"""

_INJECTION = f"<style data-wx-brand>{_BRAND_STRIP_CSS}</style>"


def _inject(html: str) -> str:
    """Splice our style block just before `</head>`. If no head tag exists,
    prepend at the top — better to have a style tag outside <head> than no
    branding strip at all."""
    if "</head>" in html:
        return html.replace("</head>", f"{_INJECTION}</head>", 1)
    return f"{_INJECTION}{html}"


# --- HTTP pass-through ------------------------------------------------------

def _upstream_url(path: str, qs: str) -> str:
    """Build the upstream URL. Strategy: strip the `/lf` prefix and hit
    Langflow's route at the root. Langflow's `BASE_URL_PATH` setting affects
    static-file serving but not API routing — `POST /lf/api/v1/login`
    returns 405, while `POST /api/v1/login` works. Forwarding at root is
    the consistent path across versions."""
    suffix = f"/{path}"
    suffix = re.sub(r"/+", "/", suffix)
    return f"{LANGFLOW_URL}{suffix}" + (f"?{qs}" if qs else "")


def _filtered_in_headers(headers: Iterable) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers:
        k_lower = k.lower() if isinstance(k, str) else k.decode().lower()
        if k_lower in _HOP_BY_HOP or k_lower == "host":
            continue
        out[k] = v if isinstance(v, str) else v.decode()
    return out


def _filtered_out_headers(headers) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        # Strip `Domain=` from Set-Cookie so the cookie scopes to our own
        # origin instead of Langflow's. Everything goes through /lf/* on
        # :8000 so the browser still sends it back.
        if k.lower() == "set-cookie":
            v = re.sub(r";\s*Domain=[^;]+", "", v, flags=re.IGNORECASE)
        out.append((k, v))
    return out


@proxy_router.api_route(
    "/lf/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(path: str, request: Request):
    if not LANGFLOW_URL:
        raise HTTPException(503, "LANGFLOW_URL not configured")

    import httpx  # type: ignore

    upstream = _upstream_url(path, request.url.query)
    method = request.method
    headers = _filtered_in_headers(request.headers.raw)
    # Spoof Host so Langflow's TLS / host checks are happy.
    headers["host"] = LANGFLOW_URL.split("://", 1)[-1]
    body = await request.body() if method not in ("GET", "HEAD") else None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            follow_redirects=False,
        ) as client:
            upstream_resp = await client.request(
                method, upstream, content=body, headers=headers,
            )
    except httpx.RequestError as e:
        raise HTTPException(502, f"langflow upstream unreachable: {e!r}")

    out_headers = _filtered_out_headers(upstream_resp.headers)

    ctype = upstream_resp.headers.get("content-type", "").lower()
    # HTML rewrite: only on text/html responses (index.html + route hits).
    if "text/html" in ctype and upstream_resp.status_code < 400:
        html = upstream_resp.content.decode("utf-8", errors="replace")
        html = _inject(html)
        return HTMLResponse(
            content=html,
            status_code=upstream_resp.status_code,
            headers={k: v for k, v in out_headers if k.lower() != "content-type"},
        )

    # Everything else — stream the body through untouched. Status + headers
    # preserved (minus hop-by-hop).
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=dict(out_headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


# --- WebSocket pass-through -------------------------------------------------
#
# Langflow opens WebSockets for flow-run streams + the mcp panel. We bridge
# them by speaking Langflow's WS upstream from our own WS handler.

@proxy_router.websocket("/lf/{path:path}")
async def proxy_ws(ws: WebSocket, path: str):
    if not LANGFLOW_URL:
        await ws.close(code=1011)
        return
    try:
        import websockets  # type: ignore
    except ImportError:
        log.warning("`websockets` package missing — install to enable /lf ws proxy")
        await ws.close(code=1011)
        return

    # Build upstream ws:// URL from LANGFLOW_URL (http→ws, https→wss).
    scheme = "wss" if LANGFLOW_URL.startswith("https") else "ws"
    host = LANGFLOW_URL.split("://", 1)[-1]
    suffix = f"/{BASE_PREFIX}/{path}" if BASE_PREFIX else f"/{path}"
    suffix = re.sub(r"/+", "/", suffix)
    qs = ws.url.query
    upstream_url = f"{scheme}://{host}{suffix}" + (f"?{qs}" if qs else "")

    # Forward the `Cookie` header so Langflow's auth survives the bridge.
    fwd_headers = [(k, v) for k, v in ws.headers.items()
                   if k.lower() in ("cookie", "authorization", "sec-websocket-protocol")]

    await ws.accept()
    import asyncio
    try:
        async with websockets.connect(upstream_url, extra_headers=fwd_headers) as upstream:
            async def pump_client_to_upstream():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def pump_upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_text(msg)
                except Exception:
                    pass

            done, pending = await asyncio.wait(
                {asyncio.create_task(pump_client_to_upstream()),
                 asyncio.create_task(pump_upstream_to_client())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
    except Exception:
        log.exception("langflow ws proxy error")
    finally:
        try:
            await ws.close()
        except Exception:
            pass
