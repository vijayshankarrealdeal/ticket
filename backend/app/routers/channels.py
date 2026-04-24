"""Channels subpages: E-Commerce / APIs / Integrations / Logs / Sheets / Health (spec §3.9).

Build-2 surface for the API Library + API Logs + Integrations. The tool-call
executor `POST /internal/apis/:name` lives here too — it's how the default
orchestrator (and, later, Langflow flows) invoke catalog entries while leaving
an `api_logs` trail the UI can render.

`_execute_api` now does real outbound HTTP via `httpx` when the catalog spec
contains a `url`. Otherwise it falls back to echo mode so the demo + tests can
keep running without network access.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..db import get_db, jl, js
from ..deps import Me

log = logging.getLogger(__name__)

# Keys redacted from request/response bodies before they hit api_logs.
REDACT_KEYS = {"authorization", "api_key", "apikey", "password", "secret",
               "token", "x-api-key", "access_token"}

router = APIRouter(tags=["channels"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- E-Commerce ----------

@router.get("/ecommerce/stores")
async def ecomm_stores(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM ecommerce_stores WHERE workspace_id = ? ORDER BY created_at DESC",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [
        {
            "id": r["id"],
            "platform": r["platform"],
            "name": r["name"],
            "health": r["health"],
            "orders30d": r["orders_30d"],
            "lastSync": r["last_sync_at"],
            "status": r["status"],
        }
        for r in rows
    ]


@router.post("/ecommerce/stores")
async def create_store(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    sid = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO ecommerce_stores (id, workspace_id, platform, name, config, status)
        VALUES (?,?,?,?,?,'connected')
        """,
        (sid, user["workspace_id"], body.get("platform", "shopify"),
         body.get("name", "Store"), js(body.get("config", {}))),
    )
    await db.commit()
    return {"id": sid, "status": "connected"}


@router.delete("/ecommerce/stores/{sid}")
async def delete_store(sid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM ecommerce_stores WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": sid, "deleted": True}


@router.post("/ecommerce/stores/{sid}/sync")
async def sync_store(sid: str, user: dict = Me) -> dict:
    """Manual backfill trigger from the E-Commerce page's Sync button.

    Pulls real orders for Shopify stores via `channels/shopify_sync.py` when a
    `platform_credentials` row is configured; degrades to a synthetic backfill
    event otherwise so demo workspaces never see an error toast."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM ecommerce_stores WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "store not found")
    store = dict(row)

    summary: dict[str, Any] = {
        "ordersPulled": 0,
        "newOrders": 0,
        "lastSync": _now(),
        "error": None,
        "fallback": True,
    }
    if store.get("platform") == "shopify":
        from ..channels.shopify_sync import sync_store as _shopify_sync
        summary = await _shopify_sync(workspace_id=user["workspace_id"], store=store)
    else:
        # Non-Shopify connectors haven't shipped a real puller yet; still mark
        # the store synced + drop a synthetic event so the UI moves.
        await db.execute(
            "UPDATE ecommerce_stores SET last_sync_at = now() WHERE id = ?",
            (sid,),
        )
        await db.execute(
            """
            INSERT INTO ecommerce_events
                (workspace_id, store_id, type, title, sub, tone)
            VALUES (?, ?, 'sync', 'Manual sync', ?, 'info')
            """,
            (user["workspace_id"], sid, store.get("name") or store.get("platform")),
        )
        await db.commit()

    return {
        "ok": summary.get("error") is None,
        "lastSync": summary.get("lastSync") or _now(),
        "ordersPulled": summary.get("ordersPulled", 0),
        "newOrders": summary.get("newOrders", 0),
        "fallback": summary.get("fallback", True),
        "error": summary.get("error"),
    }


@router.get("/ecommerce/stats")
async def ecomm_stats(user: dict = Me) -> dict:
    """KPI tiles for the E-Commerce page — computed from ecommerce_stores and
    ecommerce_events so the UI never renders stale demo numbers."""
    db = await get_db()
    ws = user["workspace_id"]
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_ago = (now - timedelta(hours=24)).isoformat()
    two_day_ago = (now - timedelta(hours=48)).isoformat()

    cur = await db.execute(
        """
        SELECT
          COALESCE(SUM(orders_30d), 0)                               AS total_orders_30d,
          COUNT(*) FILTER (WHERE status = 'connected')               AS active_stores,
          COUNT(*) FILTER (WHERE status = 'connected' AND created_at >= ?) AS new_this_month
        FROM ecommerce_stores WHERE workspace_id = ?
        """,
        (month_start, ws),
    )
    s = dict(await cur.fetchone()) or {}

    cur = await db.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE tone IN ('danger','warning') AND at >= ?) AS failed_24h,
          COUNT(*) FILTER (WHERE tone IN ('danger','warning') AND at >= ? AND at < ?) AS failed_prev_24h
        FROM ecommerce_events WHERE workspace_id = ?
        """,
        (day_ago, two_day_ago, day_ago, ws),
    )
    e = dict(await cur.fetchone()) or {}

    cur = await db.execute(
        """
        SELECT AVG(l.latency_ms) AS avg_latency
        FROM api_logs l LEFT JOIN apis a ON a.id = l.api_id
        LEFT JOIN integrations i ON i.id = a.integration_id
        WHERE l.workspace_id = ? AND l.at >= ?
          AND (i.kind = 'ecommerce' OR a.name ILIKE '%shopify%' OR a.name ILIKE '%ecommerce%')
        """,
        (ws, day_ago),
    )
    lat_row = await cur.fetchone()
    avg_latency = float(lat_row["avg_latency"]) if lat_row and lat_row["avg_latency"] is not None else None

    failed_24h = int(e.get("failed_24h") or 0)
    failed_prev = int(e.get("failed_prev_24h") or 0)
    return {
        "totalOrders30d": int(s.get("total_orders_30d") or 0),
        "activeStores": int(s.get("active_stores") or 0),
        "newStoresThisMonth": int(s.get("new_this_month") or 0),
        "avgSyncLatencyMs": int(avg_latency) if avg_latency is not None else None,
        "failedWebhooks": failed_24h,
        "failedWebhooksDelta": failed_24h - failed_prev,
    }


@router.get("/ecommerce/events")
async def ecomm_events(limit: int = Query(50, ge=1, le=200), user: dict = Me) -> list[dict]:
    """Recent webhook activity. Returns whatever the Shopify/ingestion workers
    have appended to `ecommerce_events` (spec §3.9.2, §6)."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT e.*, s.name AS store_name
          FROM ecommerce_events e LEFT JOIN ecommerce_stores s ON s.id = e.store_id
         WHERE e.workspace_id = ?
         ORDER BY e.at DESC LIMIT ?
        """,
        (user["workspace_id"], int(limit)),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [
        {
            "id": r["id"], "type": r["type"], "title": r["title"],
            "sub": r.get("sub") or r.get("store_name") or "",
            "tone": r.get("tone"),
            "time": _rel(r.get("at")),
        }
        for r in rows
    ]


# ---------- API Library ----------

def _serialize_api(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "method": r["method"],
        "description": r.get("description"),
        "integrationId": r.get("integration_id"),
        "spec": jl(r.get("spec"), {}),
        "calls24h": r.get("calls_24h", 0),
        "p95": r.get("p95_ms", 0),
        "lastUsed": r.get("last_used_at"),
    }


def _serialize_integration(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "description": r.get("description"),
        "kind": r.get("kind", "custom"),
        "health": r.get("health", "good"),
        "config": jl(r.get("config"), {}),
    }


@router.get("/apis")
async def apis_list(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE workspace_id = ? ORDER BY created_at DESC",
        (user["workspace_id"],),
    )
    return [_serialize_api(dict(r)) for r in await cur.fetchall()]


@router.post("/apis")
async def apis_create(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    aid = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO apis (id, workspace_id, integration_id, name, method, description, spec)
        VALUES (?,?,?,?,?,?,?)
        """,
        (aid, user["workspace_id"], body.get("integrationId"),
         body.get("name", "untitled"), body.get("method", "GET"),
         body.get("description"), js(body.get("spec", {}))),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM apis WHERE id = ?", (aid,))
    return _serialize_api(dict(await cur.fetchone()))


@router.patch("/apis/{aid}")
async def apis_patch(aid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE id = ? AND workspace_id = ?",
        (aid, user["workspace_id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "api not found")
    fields: dict[str, Any] = {}
    for k_src, k_db in [("name", "name"), ("method", "method"),
                        ("description", "description"),
                        ("integrationId", "integration_id")]:
        if k_src in body:
            fields[k_db] = body[k_src]
    if "spec" in body:
        fields["spec"] = js(body["spec"])
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE apis SET {sets} WHERE id = ? AND workspace_id = ?",
            (*fields.values(), aid, user["workspace_id"]),
        )
        await db.commit()
    cur = await db.execute("SELECT * FROM apis WHERE id = ?", (aid,))
    return _serialize_api(dict(await cur.fetchone()))


@router.post("/apis/{aid}/clone")
async def apis_clone(aid: str, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE id = ? AND workspace_id = ?",
        (aid, user["workspace_id"]),
    )
    src = await cur.fetchone()
    if not src:
        raise HTTPException(404, "api not found")
    src = dict(src)
    new_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO apis (id, workspace_id, integration_id, name, method, description, spec)
        VALUES (?,?,?,?,?,?,?)
        """,
        (new_id, user["workspace_id"], src.get("integration_id"),
         f"{src['name']} (copy)", src["method"], src.get("description"),
         js(jl(src.get("spec"), {}))),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM apis WHERE id = ?", (new_id,))
    return _serialize_api(dict(await cur.fetchone()))


@router.delete("/apis/{aid}")
async def apis_delete(aid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM apis WHERE id = ? AND workspace_id = ?",
        (aid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": aid, "deleted": True}


@router.post("/apis/{aid}/test")
async def apis_test(aid: str, body: dict | None = None, user: dict = Me) -> dict:
    # Exercises the catalog entry through the same executor the orchestrator
    # uses, so the "Test" button in the API Library matches production calls.
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE id = ? AND workspace_id = ?",
        (aid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "api not found")
    row = dict(row)
    return await _execute_api(db, user["workspace_id"], row, body or {}, customer_id=None)


# ---------- Integrations ----------

@router.get("/integrations")
async def integrations_list(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM integrations WHERE workspace_id = ? ORDER BY created_at DESC",
        (user["workspace_id"],),
    )
    return [_serialize_integration(dict(r)) for r in await cur.fetchall()]


@router.post("/integrations")
async def integrations_create(body: dict, user: dict = Me) -> dict:
    db = await get_db()
    iid = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO integrations (id, workspace_id, name, description, kind, config)
        VALUES (?,?,?,?,?,?)
        """,
        (iid, user["workspace_id"], body.get("name", "Integration"),
         body.get("description"), body.get("kind", "custom"),
         js(body.get("config", {}))),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM integrations WHERE id = ?", (iid,))
    return _serialize_integration(dict(await cur.fetchone()))


@router.delete("/integrations/{iid}")
async def integrations_delete(iid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM integrations WHERE id = ? AND workspace_id = ?",
        (iid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": iid, "deleted": True}


# ---------- API Logs ----------

@router.get("/api-logs")
async def api_logs_list(
    status: str | None = Query(None),
    apiId: str | None = Query(None, alias="apiId"),
    q: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Me,
) -> list[dict]:
    db = await get_db()
    clauses = ["l.workspace_id = ?"]
    params: list = [user["workspace_id"]]
    if status == "2xx":
        clauses.append("l.status BETWEEN 200 AND 299")
    elif status == "4xx":
        clauses.append("l.status BETWEEN 400 AND 499")
    elif status == "5xx":
        clauses.append("l.status >= 500")
    if apiId:
        clauses.append("l.api_id = ?")
        params.append(apiId)
    if q:
        clauses.append("(l.trace_id ILIKE ? OR a.name ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    sql = f"""
        SELECT l.*, a.name AS api_name
        FROM api_logs l LEFT JOIN apis a ON a.id = l.api_id
        WHERE {' AND '.join(clauses)}
        ORDER BY l.at DESC
        LIMIT {int(limit)}
    """
    cur = await db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    return [
        {
            "id": r["id"],
            "apiId": r.get("api_id"),
            "apiName": r.get("api_name") or "—",
            "customerId": r.get("customer_id"),
            "traceId": r["trace_id"],
            "status": r["status"],
            "latencyMs": r["latency_ms"],
            "at": r["at"],
        }
        for r in rows
    ]


@router.get("/api-logs/stats")
async def api_logs_stats(user: dict = Me) -> dict:
    db = await get_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur = await db.execute(
        """
        SELECT
          COUNT(*)                                              AS total,
          COUNT(*) FILTER (WHERE status BETWEEN 200 AND 299)    AS s2xx,
          COUNT(*) FILTER (WHERE status BETWEEN 400 AND 499)    AS s4xx,
          COUNT(*) FILTER (WHERE status >= 500)                 AS s5xx
        FROM api_logs WHERE workspace_id = ? AND at >= ?
        """,
        (user["workspace_id"], since),
    )
    r = dict(await cur.fetchone()) or {}
    total = int(r.get("total") or 0)
    success = int(r.get("s2xx") or 0)
    return {
        "total": total,
        "success": success,
        "error": int(r.get("s4xx") or 0) + int(r.get("s5xx") or 0),
        "successRate": round(100.0 * success / total, 2) if total else 100.0,
        "breakdown": {"2xx": success, "4xx": int(r.get("s4xx") or 0), "5xx": int(r.get("s5xx") or 0)},
    }


@router.get("/api-logs/{lid}")
async def api_log_detail(lid: str, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT l.*, a.name AS api_name
        FROM api_logs l LEFT JOIN apis a ON a.id = l.api_id
        WHERE l.id = ? AND l.workspace_id = ?
        """,
        (lid, user["workspace_id"]),
    )
    r = await cur.fetchone()
    if not r:
        raise HTTPException(404, "log not found")
    r = dict(r)
    return {
        "id": r["id"],
        "apiId": r.get("api_id"),
        "apiName": r.get("api_name") or "—",
        "customerId": r.get("customer_id"),
        "traceId": r["trace_id"],
        "status": r["status"],
        "latencyMs": r["latency_ms"],
        "at": r["at"],
        "request": jl(r.get("request"), {}),
        "response": jl(r.get("response"), {}),
    }


@router.get("/api-logs/stream")
async def api_logs_stream(limit: int = Query(1000, ge=1, le=10000),
                          user: dict = Me) -> StreamingResponse:
    """NDJSON export for the API Logs page "Download" button (spec §3.9.4, §9).
    One JSON object per line — streams straight from the DB cursor."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT l.id, l.api_id, l.customer_id, l.trace_id, l.status, l.latency_ms, l.at,
               a.name AS api_name
          FROM api_logs l LEFT JOIN apis a ON a.id = l.api_id
         WHERE l.workspace_id = ?
         ORDER BY l.at DESC LIMIT ?
        """,
        (user["workspace_id"], int(limit)),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    async def generate():
        for r in rows:
            yield json.dumps({
                "id": r["id"], "apiId": r.get("api_id"),
                "apiName": r.get("api_name") or "—", "traceId": r["trace_id"],
                "status": r["status"], "latencyMs": r["latency_ms"], "at": str(r["at"]),
            }, default=str) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=api-logs.ndjson"})


# ---------- Sheets (spec §3.9.5) ----------

# Service-account address rendered into the page so admins know which Google
# email to share their sheet with. Override via env in prod.
import os
SHEETS_SERVICE_EMAIL = os.environ.get(
    "SHEETS_SERVICE_EMAIL", "workspacex-sheets@workspacex-demo.iam.gserviceaccount.com"
)

# `/spreadsheets/d/<id>` — Google Sheets URL spreadsheet-id pattern.
_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]{16,})")


def _serialize_sheet(r: dict) -> dict:
    return {
        "id": r["id"],
        "url": r["url"],
        "name": r.get("name"),
        "tabs": jl(r.get("tabs"), []),
        "rows": int(r.get("rows") or 0),
        "status": r.get("status") or "connected",
        "lastSync": _rel(r.get("last_sync_at")),
        "lastSyncAt": r.get("last_sync_at"),
    }


@router.get("/sheets")
async def sheets(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM sheets WHERE workspace_id = ? ORDER BY created_at DESC",
        (user["workspace_id"],),
    )
    rows = [_serialize_sheet(dict(x)) for x in await cur.fetchall()]
    # Returning a dict (not just an array) so the page can show the service
    # account email even before the first sheet is connected. The frontend
    # accepts both shapes (`Array.isArray(res) ? res : res.items`).
    return {"items": rows, "serviceEmail": SHEETS_SERVICE_EMAIL}


@router.post("/sheets/validate")
async def sheets_validate(body: dict, user: dict = Me) -> dict:
    """Confirm the URL looks like a Google Sheet, extract the spreadsheet id,
    and synthesize a name + tab list. The real connector will replace this with
    a `spreadsheets.get` call against the Sheets API once OAuth ships."""
    url = (body or {}).get("url", "").strip()
    if not url:
        raise HTTPException(400, "url required")
    m = _SHEET_ID_RE.search(url)
    if not m:
        raise HTTPException(400, "not a Google Sheets URL")
    sid_part = m.group(1)
    name = body.get("name") or f"Sheet {sid_part[:6]}"
    tabs = body.get("tabs") or ["Sheet1"]
    return {"url": url, "name": name, "tabs": tabs, "rows": 0,
            "spreadsheetId": sid_part}


@router.post("/sheets")
async def sheets_create(body: dict, user: dict = Me) -> dict:
    url = (body or {}).get("url", "").strip()
    if not url:
        raise HTTPException(400, "url required")
    db = await get_db()
    sid = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO sheets (id, workspace_id, url, name, tabs, rows, status)
        VALUES (?,?,?,?,?,?,?)
        """,
        (sid, user["workspace_id"], url, body.get("name") or "Untitled sheet",
         js(body.get("tabs") or []), int(body.get("rows") or 0),
         body.get("status") or "connected"),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM sheets WHERE id = ?", (sid,))
    return _serialize_sheet(dict(await cur.fetchone()))


@router.patch("/sheets/{sid}")
async def sheets_patch(sid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM sheets WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(404, "sheet not found")
    fields: dict[str, Any] = {}
    for k in ("name", "status"):
        if k in body:
            fields[k] = body[k]
    if "tabs" in body:
        fields["tabs"] = js(body["tabs"])
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE sheets SET {sets} WHERE id = ? AND workspace_id = ?",
            (*fields.values(), sid, user["workspace_id"]),
        )
        await db.commit()
    cur = await db.execute("SELECT * FROM sheets WHERE id = ?", (sid,))
    return _serialize_sheet(dict(await cur.fetchone()))


@router.delete("/sheets/{sid}")
async def sheets_delete(sid: str, user: dict = Me) -> dict:
    db = await get_db()
    # Drop kb chunks sourced from this sheet so the agent's `kb_search` tool
    # stops returning rows from a sheet the operator removed.
    await db.execute(
        "DELETE FROM kb_chunks WHERE workspace_id = ? AND source = ?",
        (user["workspace_id"], f"sheet:{sid}"),
    )
    await db.execute(
        "DELETE FROM sheets WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    await db.commit()
    return {"id": sid, "deleted": True}


@router.post("/sheets/{sid}/sync")
async def sheets_sync(sid: str, user: dict = Me) -> dict:
    """Re-sync a sheet to `kb_chunks`. The real connector pulls each tab via
    Sheets API and breaks rows into chunks; here we synthesise N chunks per
    tab so `kb_search` has data to return for demos."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM sheets WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "sheet not found")
    sheet = dict(row)
    tabs = jl(sheet.get("tabs"), []) or ["Sheet1"]

    await db.execute(
        "DELETE FROM kb_chunks WHERE workspace_id = ? AND source = ?",
        (user["workspace_id"], f"sheet:{sid}"),
    )
    chunks_created = 0
    for tab in tabs:
        for i in range(5):  # 5 synthetic rows per tab — enough for demo recall.
            await db.execute(
                """
                INSERT INTO kb_chunks (workspace_id, source, title, body)
                VALUES (?,?,?,?)
                """,
                (user["workspace_id"], f"sheet:{sid}",
                 f"{sheet.get('name') or 'Sheet'} · {tab} · row {i+1}",
                 f"Row {i+1} from tab '{tab}' of sheet '{sheet.get('name')}' "
                 f"({sheet.get('url')}). Replace with real Sheets API rows."),
            )
            chunks_created += 1
    await db.execute(
        "UPDATE sheets SET last_sync_at = now(), rows = ? WHERE id = ?",
        (chunks_created, sid),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM sheets WHERE id = ?", (sid,))
    out = _serialize_sheet(dict(await cur.fetchone()))
    out["chunks"] = chunks_created
    return out


# ---------- Health services + incidents (spec §3.9.6) ----------

@router.get("/health/services")
async def health_services(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM health_services WHERE workspace_id = ? ORDER BY name",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "category": r.get("category"),
            "status": r["status"],
            "uptime30d": float(r.get("uptime_30d") or 0),
            "latencyP95": int(r.get("latency_p95_ms") or 0),
            "errorRate": float(r.get("error_rate") or 0),
            "lastCheck": _rel(r.get("last_check_at")),
            "accent": r.get("accent") or "#38bdf8",
            "letter": r.get("letter") or (r["name"][:1].upper() if r.get("name") else "?"),
        }
        for r in rows
    ]


@router.get("/health/services/{sid}/uptime")
async def health_service_uptime(sid: str, window: str = "30d", user: dict = Me) -> dict:
    """Return a synthetic uptime series for the bar chart. Once the health
    prober is wired the series comes from `health_checks` samples."""
    db = await get_db()
    cur = await db.execute(
        "SELECT uptime_30d, status FROM health_services WHERE id = ? AND workspace_id = ?",
        (sid, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "service not found")
    r = dict(row)
    days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}.get(window, 30)
    series = []
    base = float(r.get("uptime_30d") or 100)
    for i in range(days):
        series.append({"day": i, "uptime": round(base - (i % 5) * 0.1, 2)})
    return {"window": window, "series": series}


@router.get("/incidents")
async def incidents(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM incidents WHERE workspace_id = ? ORDER BY started_at DESC LIMIT 50",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "status": r["status"],
            "tone": r["tone"],
            "components": jl(r.get("components"), []),
            "summary": r.get("summary") or "",
            "started": _rel(r.get("started_at")),
            "resolvedAt": r.get("resolved_at"),
        }
        for r in rows
    ]


@router.post("/incidents/subscribe")
async def incidents_subscribe(body: dict, user: dict = Me) -> dict:
    email = (body or {}).get("email") or user.get("email")
    if not email:
        raise HTTPException(400, "email required")
    db = await get_db()
    cur = await db.execute(
        """
        INSERT INTO incident_subscribers (workspace_id, email)
        VALUES (?, ?)
        ON CONFLICT (workspace_id, email) DO NOTHING
        RETURNING id
        """,
        (user["workspace_id"], email),
    )
    inserted = await cur.fetchone()
    if inserted:
        # First-time subscribe — send a confirmation email through the outbox
        # so the subscriber can see the signup round-tripped. Re-subscribes
        # (unique conflict) skip the email so we don't flood repeat clicks.
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'email', ?)
            """,
            (user["workspace_id"], js({
                "workspace_id": user["workspace_id"],
                "to": email,
                "subject": "You're subscribed to status updates",
                "body": (
                    "You'll receive an email whenever a service goes degraded "
                    "or down, and again when it recovers.\n\n"
                    "Reply to unsubscribe at any time."
                ),
                "kind": "incident_subscribe",
            })),
        )
    await db.commit()
    return {"ok": True, "email": email, "newSubscriber": bool(inserted)}


@router.post("/health/probe")
async def health_probe(user: dict = Me) -> list[dict]:
    """Re-run the 'checks' button — touch every health service for this
    workspace, bump `last_check_at`, and randomly nudge the latency so the
    dashboard reflects fresh state. Real prober worker lands in Build 3."""
    import random
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM health_services WHERE workspace_id = ?",
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        lat = max(20, int(r.get("latency_p95_ms") or 100) + random.randint(-20, 20))
        await db.execute(
            "UPDATE health_services SET last_check_at = now(), latency_p95_ms = ? WHERE id = ?",
            (lat, r["id"]),
        )
    await db.commit()
    return await health_services(user)


def _rel(ts) -> str:
    if not ts:
        return "never"
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# ---------- Internal tool-call executor (spec §3.11.D, §3.11.H) ----------

def _redact(obj: Any) -> Any:
    """Strip secret-ish keys so they never land in `api_logs`."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k.lower() in REDACT_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _render_template(src: str, args: dict) -> str:
    """Replace `{{foo}}` tokens in URL / body templates with values from args.
    Missing tokens are left untouched so the operator notices."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(args.get(key, m.group(0)))
    return _TEMPLATE_RE.sub(sub, src)


async def _invoke_langflow_flow(spec: dict, args: dict) -> tuple[int, Any]:
    """POST to Langflow's `/api/v1/run/<flowId>` as the WorkspaceX superuser.

    `args` is the orchestrator's tool-call payload; we pass it as the `tweaks`
    block Langflow expects. Returns `(status_code, response_body)` so the
    caller can write the same `api_logs` row shape as the HTTP branch.
    """
    import os as _os
    flow_id = spec.get("flowId") or spec.get("flow_id")
    if not flow_id:
        return 502, {"ok": False, "error": "langflow spec missing flowId"}
    lf_url = (_os.environ.get("LANGFLOW_URL") or "").rstrip("/")
    if not lf_url:
        return 503, {"ok": False, "error": "LANGFLOW_URL not configured"}
    try:
        import httpx  # type: ignore
    except ImportError:
        return 501, {"ok": False, "error": "httpx not installed"}
    # Lazy import so channels.py doesn't pull langflow module at module load.
    from .langflow import _superuser_bearer
    bearer = await _superuser_bearer()
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    body = {
        "input_value": args.get("input") or args.get("prompt") or args.get("text") or "",
        "input_type": args.get("inputType") or "chat",
        "output_type": args.get("outputType") or "chat",
        "tweaks": args.get("tweaks") or {},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{lf_url}/api/v1/run/{flow_id}",
                                     json=body, headers=headers)
        ctype = resp.headers.get("content-type") or ""
        if "application/json" in ctype:
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, {"text": resp.text[:4000]}
        return resp.status_code, {"text": resp.text[:4000]}
    except Exception as e:
        return 599, {"ok": False, "error": repr(e)[:500]}


async def _execute_api(db, ws_id: str, api_row: dict, args: dict, customer_id: str | None) -> dict:
    """Invoke a catalog API by row. Dispatches on `spec.kind`:

    - `spec.kind == 'langflow'` → POST to `{LANGFLOW_URL}/api/v1/run/<flowId>` with
      the superuser bearer (spec §3.11.E Flow-as-a-tool). Created by the
      Langflow publish hook in `routers/langflow.py`.
    - `spec.url` set → real httpx request against the URL (with `{{token}}`
      template substitution).
    - fallthrough → echo the args so offline demos keep working.

    Every call writes to `api_logs` with secrets redacted.
    """
    started = time.perf_counter()
    # Inherit the request-scoped trace_id when present so webhook → internal
    # API → downstream chain shares one id. Fall back to a fresh trc_ when
    # the call comes from a worker with no request context.
    from ..tracing import current_trace_id
    trace_id = current_trace_id() or f"trc_{uuid.uuid4().hex[:12]}"
    spec = jl(api_row.get("spec"), {}) or {}
    method = (api_row.get("method") or spec.get("method") or "GET").upper()
    kind = (spec.get("kind") or "").lower()
    url = spec.get("url")
    response_body: Any = {"ok": True, "echo": args, "spec": _redact(spec)}
    response_headers: dict[str, str] = {}
    status_code = 200

    if kind == "langflow":
        status_code, response_body = await _invoke_langflow_flow(spec, args)
        # Normalise method so the api_logs request row tells the truth even
        # though the user-facing "method" column still says whatever the
        # catalog row had set (GET by default).
        method = "POST"
    elif url:
        try:
            import httpx  # type: ignore
            resolved_url = _render_template(url, args)
            headers = spec.get("headers") or {}
            body = spec.get("body")
            if isinstance(body, (dict, list)):
                body = json.loads(_render_template(json.dumps(body), args))
            timeout = float(spec.get("timeout") or 15)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method, resolved_url,
                    params=args if method == "GET" else None,
                    json=body if method != "GET" else None,
                    headers=headers,
                )
            status_code = resp.status_code
            response_headers = {k: v for k, v in resp.headers.items()}
            ctype = resp.headers.get("content-type") or ""
            if "application/json" in ctype:
                try:
                    response_body = resp.json()
                except Exception:
                    response_body = {"text": resp.text[:4000]}
            else:
                response_body = {"text": resp.text[:4000]}
        except ImportError:
            log.info("httpx not installed — _execute_api falling back to echo")
        except Exception as e:
            status_code = 599
            response_body = {"ok": False, "error": repr(e)[:500]}

    latency_ms = int((time.perf_counter() - started) * 1000)
    await db.execute(
        """
        INSERT INTO api_logs (workspace_id, api_id, customer_id, trace_id, status,
                              latency_ms, request, response)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (ws_id, api_row["id"], customer_id, trace_id, status_code, latency_ms,
         js(_redact({"args": args, "method": method, "name": api_row["name"],
                     "url": url, "spec": spec})),
         js(_redact(response_body if isinstance(response_body, (dict, list)) else {"value": response_body}))),
    )
    await db.execute(
        "UPDATE apis SET last_used_at = ?, calls_24h = calls_24h + 1 WHERE id = ?",
        (_now(), api_row["id"]),
    )
    await db.commit()
    return {"traceId": trace_id, "status": status_code, "latencyMs": latency_ms,
            "response": response_body, "headers": _redact(response_headers)}


@router.post("/internal/apis/{name}")
async def internal_api_call(name: str, body: dict, user: dict = Me) -> dict:
    """Invoked by the orchestrator / Langflow custom node. Resolves a catalog
    entry by name within the caller's workspace and runs `_execute_api`."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE workspace_id = ? AND name = ? LIMIT 1",
        (user["workspace_id"], name),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"api '{name}' not found")
    return await _execute_api(db, user["workspace_id"], dict(row), body or {}, customer_id=body.get("customerId") if body else None)


# ---------- API Playground (ad-hoc proxy) ----------

@router.post("/api-playground/send")
async def api_playground_send(body: dict, user: dict = Me) -> dict:
    """Proxy an ad-hoc HTTP request from the in-app API Playground.

    The browser-side playground can't call arbitrary hosts directly (CORS),
    so the SPA sends `{method, url, headers, body}` here. We run the request
    server-side via httpx, log it to `api_logs` with `api_id=NULL`, and echo
    `{traceId, status, latencyMs, body, headers}` back. Secrets are redacted
    from the persisted row.
    """
    started = time.perf_counter()
    from ..tracing import current_trace_id
    trace_id = current_trace_id() or f"trc_{uuid.uuid4().hex[:12]}"

    method = (body.get("method") or "GET").upper()
    url = (body.get("url") or "").strip()
    req_headers = body.get("headers") or {}
    req_body_raw = body.get("body")
    timeout = float(body.get("timeout") or 30)

    if not url or not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        raise HTTPException(400, f"unsupported method {method}")
    if not isinstance(req_headers, dict):
        raise HTTPException(400, "headers must be an object")

    status_code = 0
    response_body: Any = None
    response_headers: dict[str, str] = {}
    error: str | None = None

    try:
        import httpx  # type: ignore
        kwargs: dict[str, Any] = {"headers": req_headers}
        if method not in {"GET", "HEAD"} and req_body_raw not in (None, ""):
            # Accept pre-formed string bodies (the UI sends the raw textarea
            # content so operators keep full control over the payload).
            if isinstance(req_body_raw, (dict, list)):
                kwargs["json"] = req_body_raw
            else:
                kwargs["content"] = str(req_body_raw)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.request(method, url, **kwargs)
        status_code = resp.status_code
        response_headers = {k: v for k, v in resp.headers.items()}
        ctype = (resp.headers.get("content-type") or "").lower()
        # Accept application/json, application/vnd.api+json, application/ld+json,
        # anything ending in +json, or bare "json" payloads. Falls back to
        # attempting a parse opportunistically so APIs with sloppy Content-Type
        # (text/plain returning JSON) still get structured output.
        looks_jsonish = (
            "json" in ctype
            or "+json" in ctype
        )
        if looks_jsonish:
            try:
                response_body = resp.json()
            except Exception:
                response_body = resp.text[:16000]
        else:
            text = resp.text[:16000]
            stripped = text.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    response_body = resp.json()
                except Exception:
                    response_body = text
            else:
                response_body = text
    except ImportError:
        raise HTTPException(500, "httpx not installed on the server")
    except Exception as e:
        status_code = 599
        error = repr(e)[:500]
        response_body = {"ok": False, "error": error}

    latency_ms = int((time.perf_counter() - started) * 1000)
    db = await get_db()
    await db.execute(
        """
        INSERT INTO api_logs (workspace_id, api_id, customer_id, trace_id, status,
                              latency_ms, request, response)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (user["workspace_id"], None, None, trace_id, status_code, latency_ms,
         js(_redact({"source": "playground", "method": method, "url": url,
                     "headers": req_headers, "body": req_body_raw})),
         js(_redact(response_body if isinstance(response_body, (dict, list))
                    else {"value": response_body}))),
    )
    await db.commit()

    return {
        "traceId": trace_id,
        "status": status_code,
        "latencyMs": latency_ms,
        "body": response_body,
        "headers": _redact(response_headers),
        "ok": 200 <= status_code < 300,
        **({"error": error} if error else {}),
    }
