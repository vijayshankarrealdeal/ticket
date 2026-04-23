"""Trace ID propagation (spec cross-cutting tracing).

A single middleware stamps every request with a `trace_id`, exposes it to
downstream code via a contextvar, and echoes it on the response so clients
can correlate. Compatible with W3C `traceparent` — we extract the 32-char
trace-id from an incoming header when present, and mint a fresh UUID-hex
otherwise.

Downstream consumers (currently just `api_logs.trace_id`) read
`current_trace_id()` to stash the same id on the row, so a webhook-triggered
DB write → WS push chain all share one id.
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger(__name__)

_trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def current_trace_id() -> str:
    """Return the trace id for the current task, or an empty string if the
    middleware hasn't stamped one yet (e.g. worker-initiated writes)."""
    return _trace_id_ctx.get()


def _parse_traceparent(header: str) -> str | None:
    # W3C traceparent: `00-<trace-id>-<parent-id>-<flags>`.
    try:
        parts = header.split("-")
        if len(parts) >= 3 and len(parts[1]) == 32:
            return parts[1]
    except Exception:
        pass
    return None


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = (
            _parse_traceparent(request.headers.get("traceparent", ""))
            or request.headers.get("x-trace-id")
            or request.headers.get("x-request-id")
            or uuid.uuid4().hex
        )
        token = _trace_id_ctx.set(incoming)
        try:
            response = await call_next(request)
        finally:
            _trace_id_ctx.reset(token)
        response.headers["X-Trace-Id"] = incoming
        # Echo a W3C traceparent too so downstream services can join.
        response.headers["traceparent"] = f"00-{incoming}-{uuid.uuid4().hex[:16]}-01"
        return response
