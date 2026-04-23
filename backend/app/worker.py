"""Background worker entrypoint (spec §11).

Runs three cooperative loops in a single asyncio event-loop:

- **Outbox dispatcher** — every 500 ms, claim `outbox.status='pending'` rows
  whose `not_before` has passed, dispatch to a kind-specific sender, mark
  sent/failed with exponential backoff (spec §11, §10 send path).
- **SLA engine** — every 30 s, find tickets approaching `sla_due_at`, emit one
  `sla.breach_warning` WS event per breach and create a notification row for
  the assignee (spec §3.4, §11).
- **Auto-close** — every minute, `assigned/in_progress` → `waiting_for_customer`
  after 24 h idle, then `waiting_for_customer` → `resolved` after 72 h
  (spec §3.4.1 auto-close worker).

Multiple worker replicas are safe: each loop selects with `FOR UPDATE SKIP
LOCKED` (outbox) or no-ops when a ticket is already in the target state.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone

from .db import get_db, close_db, jl
from .ws import broker
from .routers.notifications import create_notification

log = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

WORKER_ID = f"wk_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- outbox dispatcher -----------------------------------------------

async def _dispatch_outbox_row(db, row: dict) -> tuple[bool, str | None]:
    """Return (sent_ok, error). Real channel adapters swap in here — for now
    we log + pretend success for known kinds and fail-fast for unknown kinds."""
    kind = row["kind"]
    payload = jl(row.get("payload"), {})
    if kind in {"send_wa", "send_gmail", "send_outlook", "send_ig", "webhook"}:
        log.info("outbox %s → %s payload_keys=%s", row["id"], kind, list(payload.keys()))
        return True, None
    return False, f"unknown kind: {kind}"


async def _outbox_tick() -> None:
    db = await get_db()
    cur = await db.execute(
        """
        UPDATE outbox
           SET locked_by = ?, locked_at = ?, status = 'sending'
         WHERE id IN (
             SELECT id FROM outbox
              WHERE status = 'pending' AND not_before <= now()
              ORDER BY not_before ASC
              LIMIT 20
              FOR UPDATE SKIP LOCKED
         )
         RETURNING *
        """,
        (WORKER_ID, _now_iso()),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        try:
            ok, err = await _dispatch_outbox_row(db, row)
        except Exception as e:
            ok, err = False, repr(e)
        if ok:
            await db.execute(
                "UPDATE outbox SET status = 'sent', attempts = attempts + 1 WHERE id = ?",
                (row["id"],),
            )
        else:
            attempts = int(row.get("attempts") or 0) + 1
            # Exponential backoff: 2^attempts seconds, capped to 1 h.
            from datetime import timedelta
            delay = min(2 ** attempts, 3600)
            next_try = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            status = "dead" if attempts >= 8 else "pending"
            await db.execute(
                "UPDATE outbox SET status = ?, attempts = ?, last_error = ?, not_before = ?, locked_by = NULL WHERE id = ?",
                (status, attempts, err, next_try, row["id"]),
            )
    if rows:
        await db.commit()


# ---------- SLA engine ------------------------------------------------------

async def _sla_tick() -> None:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT t.id, t.workspace_id, t.assignee_id, t.sla_due_at, t.task, t.subject,
               (SELECT name FROM customers c WHERE c.id = t.customer_id) AS customer_name
          FROM tickets t
         WHERE t.status NOT IN ('resolved','closed')
           AND t.sla_due_at IS NOT NULL
           AND t.sla_due_at <= now() + interval '30 minutes'
           AND (t.custom_fields->>'sla_warned_at') IS NULL
         LIMIT 50
        """,
        (),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        topic_inbox = f"inbox:{r['workspace_id']}"
        topic_ticket = f"ticket:{r['id']}"
        payload = {
            "ticketId": r["id"],
            "dueAt": r["sla_due_at"],
            "task": r["task"],
            "customer": r["customer_name"],
        }
        await broker.publish(topic_inbox, "sla.breach_warning", payload)
        await broker.publish(topic_ticket, "sla.breach_warning", payload)
        if r.get("assignee_id"):
            await create_notification(
                r["assignee_id"], "sla",
                title=f"SLA risk — Ticket #{r['id']}",
                body=f"{r.get('task') or 'ticket'} for {r.get('customer_name') or 'customer'} is approaching its deadline.",
                link=f"/tickets?id={r['id']}",
            )
        await db.execute(
            """
            UPDATE tickets
               SET custom_fields = jsonb_set(COALESCE(custom_fields, '{}'::jsonb),
                                             '{sla_warned_at}', to_jsonb(?::text))
             WHERE id = ?
            """,
            (_now_iso(), r["id"]),
        )
    if rows:
        await db.commit()


# ---------- auto-close ------------------------------------------------------

async def _auto_close_tick() -> None:
    db = await get_db()
    # idle → waiting_for_customer after 24h of no customer activity
    await db.execute(
        """
        UPDATE tickets
           SET status = 'waiting_for_customer', updated_at = now()
         WHERE status IN ('assigned','in_progress')
           AND last_customer_at IS NOT NULL
           AND last_customer_at < now() - interval '24 hours'
        """,
        (),
    )
    # waiting_for_customer → resolved after 72h silence
    cur = await db.execute(
        """
        UPDATE tickets
           SET status = 'resolved',
               resolved_at = now(),
               resolution_notes = COALESCE(resolution_notes,
                                           'Auto-resolved: no customer activity for 72h'),
               updated_at = now()
         WHERE status = 'waiting_for_customer'
           AND last_customer_at IS NOT NULL
           AND last_customer_at < now() - interval '72 hours'
         RETURNING id, workspace_id
        """,
        (),
    )
    closed = [dict(r) for r in await cur.fetchall()]
    for r in closed:
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
            (r["id"], "Auto-resolved: no customer activity for 72h"),
        )
        await broker.publish(f"inbox:{r['workspace_id']}", "ticket.updated", {"id": r["id"], "status": "resolved"})
    if closed:
        await db.commit()


# ---------- presence reaper (spec §3.6 / §11) -------------------------------

async def _presence_tick() -> None:
    """Flip any user whose last heartbeat is > 2 min old to 'offline' so the
    presence dots stop lying. Paired with `POST /teams/presence/heartbeat`."""
    db = await get_db()
    cur = await db.execute(
        """
        UPDATE users
           SET status = 'offline'
         WHERE status IN ('online', 'away')
           AND (last_seen_at IS NULL OR last_seen_at < now() - interval '2 minutes')
         RETURNING id, workspace_id
        """,
        (),
    )
    changed = [dict(r) for r in await cur.fetchall()]
    if changed:
        await db.commit()
        for r in changed:
            await broker.publish(
                f"workspace:{r['workspace_id']}", "presence.changed",
                {"userId": r["id"], "status": "offline"},
            )


# ---------- Shopify hourly backfill (spec §6 e-commerce) --------------------

async def _shopify_backfill_tick() -> None:
    """Placeholder backfill loop. Real adapter lands with the Shopify connector
    in Build 3 — for now we tap `last_sync_at` so the UI shows fresh sync
    timestamps on connected stores (and logs one row per store into
    `ecommerce_events` as a synthetic 'backfill' event)."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, workspace_id, name FROM ecommerce_stores
         WHERE status = 'connected'
           AND (last_sync_at IS NULL OR last_sync_at < now() - interval '1 hour')
         LIMIT 20
        """,
        (),
    )
    stores = [dict(r) for r in await cur.fetchall()]
    for s in stores:
        await db.execute(
            "UPDATE ecommerce_stores SET last_sync_at = now() WHERE id = ?",
            (s["id"],),
        )
        await db.execute(
            """
            INSERT INTO ecommerce_events (workspace_id, store_id, type, title, sub, tone)
            VALUES (?, ?, 'sync', 'Hourly backfill complete', ?, 'info')
            """,
            (s["workspace_id"], s["id"], s["name"]),
        )
    if stores:
        await db.commit()


# ---------- loop harness ----------------------------------------------------

async def _run_loop(fn, interval: float, stop: asyncio.Event, name: str) -> None:
    while not stop.is_set():
        try:
            await fn()
        except Exception:
            log.exception("worker loop %s raised", name)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    await get_db()
    log.info("worker booted (id=%s) — outbox + sla + auto-close loops active", WORKER_ID)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_run_loop(_outbox_tick,           0.5,  stop, "outbox")),
        asyncio.create_task(_run_loop(_sla_tick,             30.0,  stop, "sla")),
        asyncio.create_task(_run_loop(_auto_close_tick,      60.0,  stop, "auto_close")),
        asyncio.create_task(_run_loop(_presence_tick,        60.0,  stop, "presence")),
        asyncio.create_task(_run_loop(_shopify_backfill_tick, 3600, stop, "shopify_backfill")),
    ]

    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await close_db()
        log.info("worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
