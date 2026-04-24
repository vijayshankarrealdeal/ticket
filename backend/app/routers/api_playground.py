"""API Playground — Postman-style collections, environments, and schemas.

Lives alongside /api-playground/send (in channels.py) and adds:

- /api-playground/folders        — collections that group saved endpoints
- /api-playground/endpoints      — saved request definitions inside a folder
- /api-playground/environments   — named variable scopes (Dev / Staging / Prod)
- /api-playground/variables      — {{key}} substitutions used when sending
- /api-playground/endpoints/{id}/infer-schema — infer a JSON output schema
  from an arbitrary response body and persist it on the endpoint row.

All rows are scoped by `workspace_id` and nothing here touches the outbound
HTTP path — callers still invoke `/api-playground/send` to actually run a
request. The UI substitutes {{vars}} client-side before calling /send, so
this router only manages the stored definitions.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import get_db, jl, js
from ..deps import Me

router = APIRouter(tags=["api-playground"])


def _ts(value: Any) -> Any:
    """Pass a timestamp through to the JSON encoder. The asyncpg compat shim
    may hand back either a `datetime` or an ISO8601 `str` depending on codec
    config — FastAPI serializes either correctly, so we just return it."""
    return value


def _serialize_folder(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "parentId": str(r["parent_id"]) if r.get("parent_id") else None,
        "sortOrder": r.get("sort_order") or 0,
        "createdAt": _ts(r.get("created_at")),
    }


def _serialize_endpoint(r: dict) -> dict:
    raw_schema = r.get("output_schema")
    schema: Any = None
    if raw_schema is not None:
        decoded = jl(raw_schema, default={"__missing__": True})
        schema = decoded if decoded != {"__missing__": True} else None

    raw_tool = r.get("tool_spec")
    tool_spec: Any = None
    if raw_tool is not None:
        decoded_tool = jl(raw_tool, default={"__missing__": True})
        tool_spec = decoded_tool if decoded_tool != {"__missing__": True} else None

    return {
        "id": str(r["id"]),
        "folderId": str(r["folder_id"]) if r.get("folder_id") else None,
        "name": r["name"],
        "description": r.get("description") or "",
        "method": r["method"],
        "url": r["url"] or "",
        "headers": jl(r.get("headers"), default=[]) or [],
        "body": r.get("body") or "",
        "outputSchema": schema,
        "toolSpec": tool_spec,
        "lastStatus": r.get("last_status"),
        "lastRunAt": _ts(r.get("last_run_at")),
        "createdAt": _ts(r.get("created_at")),
        "updatedAt": _ts(r.get("updated_at")),
    }


def _schema_to_paths(node: Any, prefix: str = "", out: list[str] | None = None) -> list[str]:
    """Walk an include-flagged schema and return only the paths whose node and
    every ancestor have `include=True`. Arrays render as `key[]`, nested
    objects as `parent.child`. Used to ship a flat path list to agent tools so
    they don't have to traverse the schema themselves."""
    if out is None:
        out = []
    if not isinstance(node, dict) or not node.get("include", True):
        return out
    t = node.get("type")
    if t == "object":
        props = node.get("properties") or {}
        if not props and prefix:
            out.append(prefix)
        for k, child in props.items():
            child_prefix = f"{prefix}.{k}" if prefix else k
            _schema_to_paths(child, child_prefix, out)
        return out
    if t == "array":
        items = node.get("items") or {}
        next_prefix = f"{prefix}[]" if prefix else "[]"
        # If items is a leaf or has no further properties, emit the array itself.
        if not isinstance(items, dict) or items.get("type") not in ("object", "array"):
            if prefix:
                out.append(next_prefix)
            return out
        _schema_to_paths(items, next_prefix, out)
        return out
    # Leaf
    if prefix:
        out.append(prefix)
    return out


def _compute_tool_spec(row: dict, schema_obj: Any | None) -> dict:
    """Build the minimal payload an agent runtime needs to call this endpoint
    as a tool. Headers are kept verbatim because they often hold the auth
    cred — operators are responsible for pruning anything they don't want
    handed to the agent runtime."""
    headers_list = jl(row.get("headers"), default=[]) or []
    if isinstance(headers_list, dict):
        headers_list = [{"key": k, "value": v} for k, v in headers_list.items()]
    paths = _schema_to_paths(schema_obj) if schema_obj is not None else []
    return {
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "method": (row.get("method") or "GET").upper(),
        "url": row.get("url") or "",
        "headers": [
            {"key": (h.get("key") or "").strip(), "value": h.get("value") or ""}
            for h in headers_list
            if isinstance(h, dict) and (h.get("key") or "").strip()
        ],
        "body": row.get("body") or "",
        "outputPaths": paths,
    }


def _serialize_env(r: dict, variables: list[dict] | None = None) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "createdAt": _ts(r.get("created_at")),
        "variables": variables or [],
    }


def _serialize_var(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "environmentId": str(r["environment_id"]),
        "key": r["key"],
        "value": r["value"] or "",
        "secret": bool(r.get("secret")),
    }


# ---------- Folders ----------

@router.get("/api-playground/folders")
async def pg_folders_list(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_folders WHERE workspace_id = ? ORDER BY sort_order, created_at",
        (user["workspace_id"],),
    )
    return [_serialize_folder(dict(r)) for r in await cur.fetchall()]


@router.post("/api-playground/folders")
async def pg_folders_create(body: dict, user: dict = Me) -> dict:
    name = (body.get("name") or "").strip() or "Untitled folder"
    parent = body.get("parentId") or None
    fid = str(uuid.uuid4())
    db = await get_db()
    await db.execute(
        "INSERT INTO api_pg_folders (id, workspace_id, name, parent_id) VALUES (?,?,?,?)",
        (fid, user["workspace_id"], name, parent),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_folders WHERE id = ?", (fid,))
    return _serialize_folder(dict(await cur.fetchone()))


@router.patch("/api-playground/folders/{fid}")
async def pg_folders_update(fid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    if "name" in body:
        await db.execute(
            "UPDATE api_pg_folders SET name = ? WHERE id = ? AND workspace_id = ?",
            ((body.get("name") or "").strip() or "Untitled folder", fid, user["workspace_id"]),
        )
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_folders WHERE id = ?", (fid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "folder not found")
    return _serialize_folder(dict(row))


@router.delete("/api-playground/folders/{fid}")
async def pg_folders_delete(fid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM api_pg_folders WHERE id = ? AND workspace_id = ?",
        (fid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": fid, "deleted": True}


# ---------- Endpoints ----------

@router.get("/api-playground/endpoints")
async def pg_endpoints_list(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_endpoints WHERE workspace_id = ? ORDER BY created_at",
        (user["workspace_id"],),
    )
    return [_serialize_endpoint(dict(r)) for r in await cur.fetchall()]


@router.post("/api-playground/endpoints")
async def pg_endpoints_create(body: dict, user: dict = Me) -> dict:
    # Description is required on create: when an endpoint is later exposed as
    # an LLM tool the description is the primary cue the model uses to decide
    # whether to call it, so we refuse to persist an endpoint without one.
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "description is required (shown to LLMs)")
    eid = str(uuid.uuid4())
    method = (body.get("method") or "GET").upper()
    name = (body.get("name") or "").strip() or "Untitled request"
    headers_payload = body.get("headers") or []
    body_payload = body.get("body") or ""
    url_payload = body.get("url") or ""

    # No schema yet at create time — the include-list is empty.
    tool_spec = _compute_tool_spec(
        {
            "name": name, "description": description, "method": method,
            "url": url_payload, "headers": headers_payload, "body": body_payload,
        },
        None,
    )

    db = await get_db()
    await db.execute(
        """
        INSERT INTO api_pg_endpoints
            (id, workspace_id, folder_id, name, description, method, url, headers, body, tool_spec)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            eid, user["workspace_id"], body.get("folderId") or None,
            name, description, method, url_payload,
            js(headers_payload), body_payload, js(tool_spec),
        ),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_endpoints WHERE id = ?", (eid,))
    return _serialize_endpoint(dict(await cur.fetchone()))


@router.patch("/api-playground/endpoints/{eid}")
async def pg_endpoints_update(eid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_endpoints WHERE id = ? AND workspace_id = ?",
        (eid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "endpoint not found")

    # Don't let an existing description be erased — empty-string PATCHes are
    # rejected the same way create is, since the description is what the LLM
    # actually reads when picking a tool.
    if "description" in body:
        desc = (body.get("description") or "").strip()
        if not desc:
            raise HTTPException(400, "description is required (shown to LLMs)")

    fields = {
        "name": body.get("name"),
        "description": (body.get("description") or "").strip() if "description" in body else None,
        "method": (body.get("method") or None) and body["method"].upper(),
        "url": body.get("url"),
        "folder_id": body.get("folderId"),
    }
    sets: list[str] = []
    vals: list[Any] = []
    for col, val in fields.items():
        if val is not None:
            sets.append(f"{col} = ?")
            vals.append(val)
    if "headers" in body:
        sets.append("headers = ?")
        vals.append(js(body.get("headers") or []))
    if "body" in body:
        sets.append("body = ?")
        vals.append(body.get("body") or "")
    normalized_schema_for_spec: Any | None = None
    schema_changed = False
    if "outputSchema" in body:
        schema = body.get("outputSchema")
        normalized_schema_for_spec = _normalize_schema(schema) if schema is not None else None
        sets.append("output_schema = ?")
        vals.append(js(normalized_schema_for_spec) if normalized_schema_for_spec is not None else None)
        schema_changed = True

    if sets:
        sets.append("updated_at = now()")
        vals.extend([eid, user["workspace_id"]])
        await db.execute(
            f"UPDATE api_pg_endpoints SET {', '.join(sets)} WHERE id = ? AND workspace_id = ?",
            tuple(vals),
        )
        await db.commit()

    # Recompute the tool spec if any of its inputs changed. Cheap to redo and
    # keeps the denormalized snapshot in sync with the editable fields.
    spec_inputs = {"name", "description", "method", "url", "folderId", "headers", "body"}
    if schema_changed or any(k in body for k in spec_inputs):
        cur = await db.execute("SELECT * FROM api_pg_endpoints WHERE id = ?", (eid,))
        fresh = dict(await cur.fetchone())
        schema_obj = normalized_schema_for_spec
        if schema_obj is None and fresh.get("output_schema") is not None:
            decoded = jl(fresh.get("output_schema"), default={"__missing__": True})
            schema_obj = decoded if decoded != {"__missing__": True} else None
        new_spec = _compute_tool_spec(fresh, schema_obj)
        await db.execute(
            "UPDATE api_pg_endpoints SET tool_spec = ? WHERE id = ? AND workspace_id = ?",
            (js(new_spec), eid, user["workspace_id"]),
        )
        await db.commit()

    cur = await db.execute("SELECT * FROM api_pg_endpoints WHERE id = ?", (eid,))
    return _serialize_endpoint(dict(await cur.fetchone()))


@router.delete("/api-playground/endpoints/{eid}")
async def pg_endpoints_delete(eid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM api_pg_endpoints WHERE id = ? AND workspace_id = ?",
        (eid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": eid, "deleted": True}


# ---------- Output schema inference ----------

def _infer_schema(value: Any, depth: int = 0) -> dict:
    """Best-effort JSON-schema-ish descriptor with per-node `include` flags.

    Each node gets `include: True` by default so a freshly-captured schema
    exposes every field. The Schema editor in the UI lets the operator untick
    fields they don't want an LLM tool caller to see; the filter step applied
    at execution time keeps only `include=True` branches.
    """
    if depth > 8:
        return {"type": "any", "include": True}
    if value is None:
        return {"type": "null", "include": True}
    if isinstance(value, bool):
        return {"type": "boolean", "include": True}
    if isinstance(value, int):
        return {"type": "integer", "include": True}
    if isinstance(value, float):
        return {"type": "number", "include": True}
    if isinstance(value, str):
        return {"type": "string", "include": True}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {"type": "any", "include": True}, "include": True}
        return {
            "type": "array",
            "items": _infer_schema(value[0], depth + 1),
            "include": True,
        }
    if isinstance(value, dict):
        props: dict[str, dict] = {}
        for k, v in value.items():
            props[str(k)] = _infer_schema(v, depth + 1)
        return {"type": "object", "properties": props, "include": True}
    return {"type": "any", "include": True}


def _normalize_schema(node: Any) -> Any:
    """Make sure an operator-provided schema has `include` on every node so
    the filter step can't accidentally drop everything. Leaves other shape
    bits (type/properties/items) untouched."""
    if not isinstance(node, dict):
        return node
    node.setdefault("include", True)
    t = node.get("type")
    if t == "object" and isinstance(node.get("properties"), dict):
        node["properties"] = {k: _normalize_schema(v) for k, v in node["properties"].items()}
    elif t == "array" and node.get("items") is not None:
        node["items"] = _normalize_schema(node["items"])
    return node


@router.post("/api-playground/endpoints/{eid}/infer-schema")
async def pg_endpoints_infer_schema(eid: str, body: dict, user: dict = Me) -> dict:
    """Persist an inferred JSON schema for this endpoint using the sample
    response the caller provides (`{"sample": <any>}`). Only overwrites when
    the current schema is empty unless `force=true`."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_endpoints WHERE id = ? AND workspace_id = ?",
        (eid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "endpoint not found")
    row = dict(row)
    current = jl(row.get("output_schema"), default=None)
    force = bool(body.get("force"))
    if current and not force:
        return _serialize_endpoint(row)

    sample = body.get("sample")
    status = body.get("status")
    schema = _infer_schema(sample)
    spec = _compute_tool_spec(row, schema)
    await db.execute(
        """
        UPDATE api_pg_endpoints
        SET output_schema = ?, tool_spec = ?, last_status = ?,
            last_run_at = now(), updated_at = now()
        WHERE id = ? AND workspace_id = ?
        """,
        (js(schema), js(spec), status, eid, user["workspace_id"]),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_endpoints WHERE id = ?", (eid,))
    return _serialize_endpoint(dict(await cur.fetchone()))


# ---------- Environments + variables ----------

@router.get("/api-playground/environments")
async def pg_envs_list(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM api_pg_environments WHERE workspace_id = ? ORDER BY created_at",
        (user["workspace_id"],),
    )
    envs = [dict(r) for r in await cur.fetchall()]
    if not envs:
        return []
    ids = [e["id"] for e in envs]
    placeholders = ",".join(["?"] * len(ids))
    cur = await db.execute(
        f"SELECT * FROM api_pg_variables WHERE environment_id IN ({placeholders}) ORDER BY key",
        tuple(ids),
    )
    vars_rows = [dict(r) for r in await cur.fetchall()]
    grouped: dict[str, list[dict]] = {}
    for v in vars_rows:
        grouped.setdefault(str(v["environment_id"]), []).append(_serialize_var(v))
    return [_serialize_env(e, grouped.get(str(e["id"]), [])) for e in envs]


@router.post("/api-playground/environments")
async def pg_envs_create(body: dict, user: dict = Me) -> dict:
    name = (body.get("name") or "").strip() or "Environment"
    eid = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO api_pg_environments (id, workspace_id, name) VALUES (?,?,?)",
            (eid, user["workspace_id"], name),
        )
    except Exception as e:
        raise HTTPException(400, f"could not create env: {e}")
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_environments WHERE id = ?", (eid,))
    return _serialize_env(dict(await cur.fetchone()), [])


@router.patch("/api-playground/environments/{envid}")
async def pg_envs_update(envid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    if "name" in body:
        await db.execute(
            "UPDATE api_pg_environments SET name = ? WHERE id = ? AND workspace_id = ?",
            ((body.get("name") or "").strip() or "Environment", envid, user["workspace_id"]),
        )
        await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_environments WHERE id = ?", (envid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "environment not found")
    return _serialize_env(dict(row))


@router.delete("/api-playground/environments/{envid}")
async def pg_envs_delete(envid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM api_pg_environments WHERE id = ? AND workspace_id = ?",
        (envid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": envid, "deleted": True}


@router.post("/api-playground/environments/{envid}/variables")
async def pg_vars_create(envid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM api_pg_environments WHERE id = ? AND workspace_id = ?",
        (envid, user["workspace_id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "environment not found")
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    vid = str(uuid.uuid4())
    try:
        await db.execute(
            """
            INSERT INTO api_pg_variables (id, workspace_id, environment_id, key, value, secret)
            VALUES (?,?,?,?,?,?)
            """,
            (
                vid, user["workspace_id"], envid, key,
                body.get("value") or "",
                bool(body.get("secret")),
            ),
        )
    except Exception as e:
        raise HTTPException(400, f"variable '{key}' already exists: {e}")
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_variables WHERE id = ?", (vid,))
    return _serialize_var(dict(await cur.fetchone()))


@router.patch("/api-playground/variables/{vid}")
async def pg_vars_update(vid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    sets: list[str] = []
    vals: list[Any] = []
    for col in ("key", "value"):
        if col in body:
            sets.append(f"{col} = ?")
            vals.append(body.get(col) or "")
    if "secret" in body:
        sets.append("secret = ?")
        vals.append(bool(body.get("secret")))
    if not sets:
        cur = await db.execute("SELECT * FROM api_pg_variables WHERE id = ?", (vid,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "variable not found")
        return _serialize_var(dict(row))
    vals.extend([vid, user["workspace_id"]])
    await db.execute(
        f"UPDATE api_pg_variables SET {', '.join(sets)} WHERE id = ? AND workspace_id = ?",
        tuple(vals),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM api_pg_variables WHERE id = ?", (vid,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "variable not found")
    return _serialize_var(dict(row))


@router.delete("/api-playground/variables/{vid}")
async def pg_vars_delete(vid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM api_pg_variables WHERE id = ? AND workspace_id = ?",
        (vid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": vid, "deleted": True}
