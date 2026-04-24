"""Expose the API Library (api_pg_endpoints) as Langflow-importable tools.

Langflow has a first-class "OpenAPI" component: given a spec URL + API key, it
generates one callable tool per operation. This router publishes the caller's
workspace-scoped library as such a spec, plus an invoke endpoint the generated
tools point at.

Three surfaces:

- `GET  /flow/api-library/tools`            — compact JSON list (for the SPA)
- `GET  /flow/api-library/openapi.json`     — OpenAPI 3.0 spec for Langflow
- `POST /flow/api-library/invoke/{name}`    — execute a saved endpoint

Auth: `wx_session` cookie, `Authorization: Bearer`, or `?token=` query param.
The SPA shows the OpenAPI URL with a note that the operator must supply their
workspace bearer as the OpenAPI component's API key inside Langflow.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..db import get_db, jl, js
from ..deps import MaybeMe


log = logging.getLogger(__name__)

# Dev fallback matches the compose default so a host-run backend + a
# compose-run langflow work out of the box without anyone editing .env.
# Real deployments should set `WORKSPACEX_INTERNAL_KEY` to a strong value
# on both services. The `_internal_key()` helper surfaces a warning the
# first time it falls back so nobody ships the default to prod by accident.
_DEV_INTERNAL_KEY = "dev-internal-key-change-me"
_warned_default_key = False


def _internal_key() -> str:
    global _warned_default_key
    value = os.environ.get("WORKSPACEX_INTERNAL_KEY")
    if value:
        return value
    if not _warned_default_key:
        log.warning(
            "WORKSPACEX_INTERNAL_KEY is not set; falling back to the dev "
            "default. Set a strong value in .env before deploying."
        )
        _warned_default_key = True
    return _DEV_INTERNAL_KEY


router = APIRouter(prefix="/flow/api-library", tags=["langflow-api-library"])


_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _render(src: str, args: dict) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(args.get(key, m.group(0)))
    return _TEMPLATE_RE.sub(sub, src)


async def _resolve_user(request: Request, token: str | None, user: dict | None) -> dict:
    """Accept session cookie, Authorization bearer (via MaybeMe), `?token=`
    fallback, OR service-to-service internal auth.

    The internal path lets the WorkspaceX Langflow custom component call
    back without asking the user for a bearer: it presents
    `Authorization: Internal <WORKSPACEX_INTERNAL_KEY>` plus
    `X-LF-User: <langflow_user_id>`. We resolve that Langflow user id back
    to the matching WorkspaceX user via `users.langflow_user_id` — which
    was populated by `_provision_user_and_folder` at SSO time."""
    if user:
        return user

    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("internal "):
        provided = auth_header.split(" ", 1)[1].strip()
        expected = _internal_key()
        if expected and provided and hmac.compare_digest(provided, expected):
            lf_user_id = (request.headers.get("x-lf-user") or "").strip()
            # `users.langflow_user_id` is UUID-typed — asyncpg throws a cast
            # error on anything that isn't a well-formed UUID, which would
            # surface as a 500 to the component. Parse first so a bogus
            # header returns a clean 401 instead.
            try:
                parsed = uuid.UUID(lf_user_id) if lf_user_id else None
            except ValueError:
                parsed = None
            if parsed is not None:
                db = await get_db()
                cur = await db.execute(
                    "SELECT id, workspace_id, email, role FROM users "
                    "WHERE langflow_user_id = ? LIMIT 1",
                    (str(parsed),),
                )
                row = await cur.fetchone()
                if row:
                    return dict(row)
            raise HTTPException(
                401,
                "internal auth accepted, but X-LF-User "
                f"{lf_user_id!r} did not match any users.langflow_user_id",
            )

    if token:
        from .auth import session_user_from_token
        resolved = await session_user_from_token(token)
        if resolved:
            return resolved
    raise HTTPException(401, "not authenticated")


def _extract_headers(raw: Any) -> dict[str, str]:
    """Headers are stored as `[{key,value}]` in the playground; coerce to the
    flat dict httpx expects. Drops blank keys so operators can leave empty
    rows in the editor without breaking invocation."""
    out: dict[str, str] = {}
    items = jl(raw, default=[]) if not isinstance(raw, (list, dict)) else raw
    if isinstance(items, dict):
        for k, v in items.items():
            if (k or "").strip():
                out[str(k).strip()] = str(v or "")
        return out
    for h in items or []:
        if not isinstance(h, dict):
            continue
        k = (h.get("key") or "").strip()
        if not k:
            continue
        out[k] = str(h.get("value") or "")
    return out


def _operation_id(name: str) -> str:
    """Turn a display name into a safe OpenAPI operationId. Langflow uses
    this as the tool name — keep it readable."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "").strip()) or "endpoint"
    slug = slug.strip("_") or "endpoint"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    return slug[:64]


def _paths_to_schema(paths: list[str]) -> dict:
    """`outputPaths` is a flat list like `["user.id", "items[]"]`. Expand it
    into a nested JSON schema so Langflow can render the tool's return type
    in the graph. Unknown paths default to `string`."""
    root: dict[str, Any] = {"type": "object", "properties": {}}
    for path in paths or []:
        parts = re.findall(r"[^.\[\]]+|\[\]", path)
        node = root
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if part == "[]":
                node.setdefault("items", {"type": "string"} if is_last
                                         else {"type": "object", "properties": {}})
                node["type"] = "array"
                if is_last:
                    break
                node = node["items"]
                continue
            props = node.setdefault("properties", {})
            if is_last:
                props.setdefault(part, {"type": "string"})
            else:
                child = props.setdefault(part, {"type": "object", "properties": {}})
                node = child
    return root


@router.get("/tools")
async def list_tools(
    request: Request,
    token: str | None = Query(default=None),
    user: dict | None = MaybeMe,
) -> dict:
    """Compact listing for the SPA + the Langflow custom component. Returns
    the full folder list (so the component can render the Folder dropdown)
    and every saved endpoint tagged with its `folderId` (so the component
    can filter once the user picks one)."""
    user = await _resolve_user(request, token, user)
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, parent_id, sort_order FROM api_pg_folders "
        "WHERE workspace_id = ? ORDER BY sort_order, name",
        (user["workspace_id"],),
    )
    folders = [dict(r) for r in await cur.fetchall()]

    cur = await db.execute(
        "SELECT id, name, description, method, url, folder_id "
        "FROM api_pg_endpoints WHERE workspace_id = ? ORDER BY name",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    base = str(request.base_url).rstrip("/")
    return {
        "workspaceId": user["workspace_id"],
        "openapiUrl": f"{base}/flow/api-library/openapi.json",
        "invokeUrlTemplate": f"{base}/flow/api-library/invoke/{{name}}",
        "folders": [
            {
                "id": str(f["id"]),
                "name": f.get("name") or "",
                "parentId": str(f["parent_id"]) if f.get("parent_id") else None,
            }
            for f in folders
        ],
        "tools": [
            {
                "id": str(r["id"]),
                "name": r["name"] or "",
                "operationId": _operation_id(r["name"] or ""),
                "method": (r["method"] or "GET").upper(),
                "description": r.get("description") or "",
                "targetUrl": r.get("url") or "",
                "folderId": str(r["folder_id"]) if r.get("folder_id") else None,
            }
            for r in rows
        ],
    }


@router.get("/openapi.json")
async def openapi_spec(
    request: Request,
    token: str | None = Query(default=None),
    user: dict | None = MaybeMe,
) -> dict:
    """OpenAPI 3.0.3 document — one POST operation per saved endpoint, all
    pointing at `/flow/api-library/invoke/{name}`. Each operation advertises
    a `{args}` body plus the persisted `outputPaths` as a response schema
    so Langflow can wire downstream nodes without a sample run."""
    user = await _resolve_user(request, token, user)
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, description, method, url, tool_spec "
        "FROM api_pg_endpoints WHERE workspace_id = ? ORDER BY name",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    base = str(request.base_url).rstrip("/")
    paths: dict[str, dict] = {}
    seen_ops: set[str] = set()
    for r in rows:
        op_id = _operation_id(r.get("name") or "")
        # De-dup across rows with the same sanitized name — Langflow requires
        # unique operationIds.
        if op_id in seen_ops:
            op_id = f"{op_id}_{str(r['id'])[:6]}"
        seen_ops.add(op_id)

        spec = jl(r.get("tool_spec"), default=None) or {}
        output_paths = spec.get("outputPaths") or []
        response_schema = _paths_to_schema(output_paths) if output_paths else {"type": "object"}

        path_key = f"/flow/api-library/invoke/{r['name']}"
        paths[path_key] = {
            "post": {
                "operationId": op_id,
                "summary": (r.get("name") or "").strip() or op_id,
                "description": (r.get("description") or "").strip()
                               or "Invoke a saved API Library endpoint.",
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "args": {
                                        "type": "object",
                                        "description": "Template values. Replace "
                                                       "`{{key}}` tokens in the "
                                                       "saved URL / headers / body.",
                                        "additionalProperties": True,
                                    }
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Upstream response forwarded.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "integer"},
                                        "latencyMs": {"type": "integer"},
                                        "body": response_schema,
                                    },
                                }
                            }
                        },
                    }
                },
                "security": [{"bearerAuth": []}],
                "x-workspacex": {
                    "endpointId": str(r["id"]),
                    "upstreamMethod": (r.get("method") or "GET").upper(),
                    "upstreamUrl": r.get("url") or "",
                },
            }
        }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "WorkspaceX API Library",
            "description": "Saved API Playground endpoints, exposed as tools. "
                           "Import this URL with Langflow's OpenAPI component "
                           "(one tool per operation).",
            "version": "1.0.0",
        },
        "servers": [{"url": base}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "WorkspaceX session token.",
                }
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": paths,
    }


@router.post("/invoke/{name}")
async def invoke_tool(
    name: str,
    body: dict | None,
    request: Request,
    token: str | None = Query(default=None),
    user: dict | None = MaybeMe,
) -> dict:
    """Execute a saved endpoint by name. Payload shape:

        { "args": { "foo": "bar" } }

    `args` values are substituted into `{{tokens}}` in the stored URL,
    headers, and body. GET/HEAD requests get `args` as query params as well
    so operators can pass values without rewriting the saved URL."""
    user = await _resolve_user(request, token, user)
    args = (body or {}).get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(400, "args must be an object")

    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_endpoints WHERE workspace_id = ? AND name = ? LIMIT 1",
        (user["workspace_id"], name),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"endpoint '{name}' not found")
    row = dict(row)

    method = (row.get("method") or "GET").upper()
    url = _render(row.get("url") or "", args)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "endpoint url missing scheme — fix in the playground")

    headers = {k: _render(v, args) for k, v in _extract_headers(row.get("headers")).items()}
    raw_body = row.get("body") or ""
    rendered_body: Any = _render(raw_body, args) if raw_body else ""

    started = time.perf_counter()
    from ..tracing import current_trace_id
    trace_id = current_trace_id() or f"trc_{uuid.uuid4().hex[:12]}"

    status = 0
    resp_body: Any = None
    resp_headers: dict[str, str] = {}
    error: str | None = None
    try:
        import httpx  # type: ignore
        kwargs: dict[str, Any] = {"headers": headers}
        if method in {"GET", "HEAD"}:
            # Scalars in `args` become query params. Nested objects are
            # skipped because httpx can't encode them as a single ?k=v pair.
            kwargs["params"] = {k: v for k, v in args.items()
                                if isinstance(v, (str, int, float, bool))}
        elif rendered_body:
            stripped = rendered_body.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    kwargs["json"] = json.loads(rendered_body)
                except Exception:
                    kwargs["content"] = rendered_body
            else:
                kwargs["content"] = rendered_body

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(method, url, **kwargs)
        status = resp.status_code
        resp_headers = dict(resp.headers)
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" in ctype or "+json" in ctype:
            try:
                resp_body = resp.json()
            except Exception:
                resp_body = resp.text[:16000]
        else:
            text = resp.text[:16000]
            stripped = text.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    resp_body = resp.json()
                except Exception:
                    resp_body = text
            else:
                resp_body = text
    except ImportError:
        raise HTTPException(500, "httpx not installed on the server")
    except Exception as e:
        status = 599
        error = repr(e)[:500]
        resp_body = {"ok": False, "error": error}

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Persist to api_logs with api_id=NULL since this isn't a catalog row; the
    # library endpoint id lives in the request blob so API Logs can still show
    # "invoked via Langflow tool".
    from .channels import _redact  # lazy to avoid circular import at module load
    await db.execute(
        """
        INSERT INTO api_logs (workspace_id, api_id, customer_id, trace_id, status,
                              latency_ms, request, response)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (user["workspace_id"], None, None, trace_id, status, latency_ms,
         js(_redact({"source": "api-library-tool", "endpointId": str(row["id"]),
                     "name": row["name"], "method": method, "url": url,
                     "headers": headers, "args": args})),
         js(_redact(resp_body if isinstance(resp_body, (dict, list))
                    else {"value": resp_body}))),
    )
    await db.execute(
        "UPDATE api_pg_endpoints SET last_status = ?, last_run_at = now() "
        "WHERE id = ? AND workspace_id = ?",
        (status, row["id"], user["workspace_id"]),
    )
    await db.commit()

    return {
        "traceId": trace_id,
        "status": status,
        "latencyMs": latency_ms,
        "ok": 200 <= status < 300,
        "body": resp_body,
        "headers": _redact(resp_headers),
        **({"error": error} if error else {}),
    }
