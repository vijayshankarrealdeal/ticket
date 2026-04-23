"""Shopify Admin API backfill (spec §6 e-commerce, §3.9.2).

Used by both the manual `POST /ecommerce/stores/:id/sync` button on the
E-Commerce page and the hourly `_shopify_backfill_tick` worker.

Resolution order for credentials:

1. `platform_credentials(provider='shopify', workspace_id=...)` row matching
   the store's shop domain via `account_id` or `meta.shop_domain`. Pulls the
   admin token from `secrets.access_token` (custom-app or OAuth install).
2. If no row matches, the helper falls back to a synthetic backfill — bumps
   `last_sync_at` and appends one `ecommerce_events` row so the UI still
   shows fresh activity. Mirrors how the send-* outbox adapters degrade to
   `delivery_log` when creds are missing: the demo never breaks.

The real call is a paginated `GET /admin/api/{API_VERSION}/orders.json` with
`status=any&updated_at_min=<since>` (Shopify's pagination cursor lives in the
`Link` header). We page until we've pulled at most 250 orders per sweep — the
hourly worker resumes from `last_sync_at` so a busier store catches up over
several ticks rather than blocking startup.

Each order is upserted into `orders` using the same shape the webhook handler
in `routers/webhooks.py` writes, so the activity feed + customer rollups
behave identically whether ingest came via push or pull.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from ..db import get_db, js, jl
from ..routers.conversations import resolve_or_create_customer

log = logging.getLogger(__name__)

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-01")
MAX_ORDERS_PER_SWEEP = 250
HTTP_TIMEOUT = 20.0


def _shop_domain(store: dict, creds: dict | None) -> str | None:
    """Pull the *.myshopify.com host out of the store row + cred row."""
    cfg = jl(store.get("config"), {}) or {}
    cand = (
        cfg.get("shop_domain")
        or cfg.get("shopDomain")
        or cfg.get("domain")
        or cfg.get("shop")
        or (creds or {}).get("account_id")
        or jl((creds or {}).get("meta"), {}).get("shop_domain")
    )
    if not cand:
        return None
    cand = str(cand).strip().lower()
    cand = re.sub(r"^https?://", "", cand).rstrip("/")
    if not cand.endswith(".myshopify.com") and "." not in cand:
        cand = f"{cand}.myshopify.com"
    return cand


def _access_token(creds: dict | None) -> str | None:
    if not creds:
        return None
    secrets = jl(creds.get("secrets"), {}) or {}
    return (
        secrets.get("access_token")
        or secrets.get("admin_token")
        or secrets.get("token")
    )


async def _load_creds(db, workspace_id: str, store: dict) -> dict | None:
    cfg = jl(store.get("config"), {}) or {}
    domain_hint = (
        cfg.get("shop_domain") or cfg.get("shopDomain") or cfg.get("domain")
    )
    cur = await db.execute(
        """
        SELECT id, provider, account_id, status, secrets, meta
          FROM platform_credentials
         WHERE workspace_id = ? AND provider = 'shopify'
         ORDER BY created_at DESC
        """,
        (workspace_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        return None
    if domain_hint:
        norm = str(domain_hint).strip().lower()
        for r in rows:
            if (r.get("account_id") or "").lower() == norm:
                return r
    return rows[0]


async def _upsert_order(db, *, workspace_id: str, store_id: str, order: dict) -> bool:
    """Returns True when this is a brand-new order id (drives orders_30d)."""
    cust = order.get("customer") or {}
    customer_id = await resolve_or_create_customer(
        workspace_id=workspace_id,
        email=cust.get("email"),
        phone=cust.get("phone"),
        shopify_id=str(cust.get("id")) if cust.get("id") else None,
        name=((cust.get("first_name") or "") + " " + (cust.get("last_name") or "")).strip()
        or None,
    )
    oid = str(order.get("id") or order.get("name") or uuid4())
    items = [
        {
            "name": li.get("title") or li.get("name"),
            "qty": li.get("quantity", 1),
            "price": li.get("price"),
        }
        for li in (order.get("line_items") or [])
    ]
    total = order.get("total_price") or order.get("current_total_price") or 0
    placed = order.get("created_at") or datetime.now(timezone.utc).isoformat()
    cur = await db.execute("SELECT 1 FROM orders WHERE id = ?", (oid,))
    is_new = (await cur.fetchone()) is None
    await db.execute(
        """
        INSERT INTO orders (id, workspace_id, customer_id, source, placed_at,
                            total, currency, status, status_tone, items)
        VALUES (?,?,?, 'shopify', ?, ?, ?, ?, 'info', ?)
        ON CONFLICT (id) DO UPDATE
           SET status = EXCLUDED.status,
               total  = EXCLUDED.total,
               items  = EXCLUDED.items
        """,
        (
            oid, workspace_id, customer_id, placed, float(total or 0),
            order.get("currency") or "USD",
            order.get("financial_status") or "paid", js(items),
        ),
    )
    return is_new


def _next_link(link_header: str | None) -> str | None:
    """Parse Shopify's `Link` header to find the next-page cursor URL."""
    if not link_header:
        return None
    for part in link_header.split(","):
        bits = part.strip().split(";")
        if len(bits) < 2:
            continue
        url = bits[0].strip().strip("<>")
        rels = ";".join(bits[1:]).strip()
        if 'rel="next"' in rels:
            return url
    return None


async def sync_store(
    *,
    workspace_id: str,
    store: dict,
    since: datetime | None = None,
) -> dict:
    """Backfill orders from a single Shopify store.

    Returns a summary dict the API endpoint hands straight back to the SPA::

        {"ordersPulled": N, "newOrders": M, "lastSync": <iso>,
         "error": str | None, "fallback": bool}

    Never raises. Errors are surfaced in `error` so the UI can render a toast
    without blowing up the page.
    """
    db = await get_db()
    store_id = store["id"]
    creds = await _load_creds(db, workspace_id, store)
    domain = _shop_domain(store, creds)
    token = _access_token(creds)

    summary: dict[str, Any] = {
        "ordersPulled": 0,
        "newOrders": 0,
        "lastSync": None,
        "error": None,
        "fallback": False,
    }

    # ---- Fallback path: no creds → synthetic backfill, never blow up ----
    if not (domain and token):
        summary["fallback"] = True
        await db.execute(
            "UPDATE ecommerce_stores SET last_sync_at = now() WHERE id = ?",
            (store_id,),
        )
        await db.execute(
            """
            INSERT INTO ecommerce_events
                (workspace_id, store_id, type, title, sub, tone)
            VALUES (?, ?, 'sync', ?, ?, 'info')
            """,
            (
                workspace_id, store_id,
                "Manual sync (demo mode)",
                "No Shopify credentials connected; configure platform_credentials "
                "to pull real orders.",
            ),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT last_sync_at FROM ecommerce_stores WHERE id = ?", (store_id,)
        )
        r = await cur.fetchone()
        summary["lastSync"] = r["last_sync_at"] if r else None
        return summary

    try:
        import httpx  # type: ignore
    except ImportError:
        summary["error"] = "httpx not installed"
        summary["fallback"] = True
        return summary

    if since is None:
        # Default window: anything updated since the last sync (with a bit of
        # overlap to absorb webhook gaps), capped to 30 days for first runs.
        last = store.get("last_sync_at")
        if isinstance(last, datetime):
            since = last - timedelta(minutes=5)
        else:
            since = datetime.now(timezone.utc) - timedelta(days=30)

    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url: str | None = (
        f"https://{domain}/admin/api/{API_VERSION}/orders.json"
        f"?status=any&limit=50&updated_at_min={since.astimezone(timezone.utc).isoformat()}"
    )

    pulled = 0
    new_orders = 0
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while url and pulled < MAX_ORDERS_PER_SWEEP:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (401, 403):
                    summary["error"] = (
                        f"shopify auth failed ({resp.status_code}); "
                        "credential needs reconnect"
                    )
                    await db.execute(
                        """
                        UPDATE platform_credentials
                           SET status = 'reauth_required'
                         WHERE id = ?
                        """,
                        (creds["id"],),
                    )
                    break
                if resp.status_code >= 400:
                    summary["error"] = f"shopify {resp.status_code}: {resp.text[:200]}"
                    break

                payload = resp.json() if resp.content else {}
                orders = payload.get("orders") or []
                for order in orders:
                    try:
                        is_new = await _upsert_order(
                            db,
                            workspace_id=workspace_id,
                            store_id=store_id,
                            order=order,
                        )
                        pulled += 1
                        if is_new:
                            new_orders += 1
                    except Exception:
                        log.exception("shopify upsert failed for order %s", order.get("id"))

                url = _next_link(resp.headers.get("Link"))
    except Exception as err:
        log.exception("shopify sync failed for store %s", store_id)
        summary["error"] = str(err)

    # Always advance last_sync_at so a partial failure doesn't cause the next
    # sweep to refetch the same stale window — the operator can hit Sync again
    # from the UI to retry, and the worker tries again on its hourly cadence.
    await db.execute(
        """
        UPDATE ecommerce_stores
           SET last_sync_at = now(),
               orders_30d = (
                 SELECT COUNT(*) FROM orders
                  WHERE workspace_id = ?
                    AND source = 'shopify'
                    AND placed_at >= now() - interval '30 days'
               )
         WHERE id = ?
        """,
        (workspace_id, store_id),
    )

    title = (
        f"Pulled {pulled} order{'s' if pulled != 1 else ''}"
        if not summary["error"]
        else "Sync failed"
    )
    sub = (
        f"{new_orders} new · since {since.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        if not summary["error"]
        else summary["error"][:200]
    )
    await db.execute(
        """
        INSERT INTO ecommerce_events
            (workspace_id, store_id, type, title, sub, tone)
        VALUES (?, ?, 'sync', ?, ?, ?)
        """,
        (workspace_id, store_id, title, sub,
         "warning" if summary["error"] else "info"),
    )
    await db.commit()

    cur = await db.execute(
        "SELECT last_sync_at FROM ecommerce_stores WHERE id = ?", (store_id,)
    )
    r = await cur.fetchone()
    summary["lastSync"] = r["last_sync_at"] if r else None
    summary["ordersPulled"] = pulled
    summary["newOrders"] = new_orders
    return summary
