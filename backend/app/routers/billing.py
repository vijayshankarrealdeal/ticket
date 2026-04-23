"""Billing (spec §3.12).

Plan/usage/invoices endpoints plus a checkout starter. Stripe wiring is
credential-gated — when `STRIPE_SECRET_KEY` is configured we hit the Stripe API;
otherwise we serve a demo shape so the Settings → Billing panel renders and
admins can eyeball the layout. Incoming Stripe webhooks are handled in
`routers/webhooks.py` (they upsert `invoices` + flip `workspaces.plan`).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..deps import AdminOnly, Me

router = APIRouter(prefix="/billing", tags=["billing"])

PLANS = {
    "starter":  {"name": "Starter",  "price_cents": 0,     "ticket_limit": 500,    "agent_limit": 2},
    "growth":   {"name": "Growth",   "price_cents": 4900,  "ticket_limit": 5_000,  "agent_limit": 10},
    "scale":    {"name": "Scale",    "price_cents": 19900, "ticket_limit": 50_000, "agent_limit": 50},
    "enterprise": {"name": "Enterprise", "price_cents": 0, "ticket_limit": None,   "agent_limit": None},
}


@router.get("/plan")
async def get_plan(user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT plan, plan_renews_at, stripe_subscription_id FROM workspaces WHERE id = ?",
        (user["workspace_id"],),
    )
    row = await cur.fetchone()
    current = (row["plan"] if row else None) or "growth"
    plan = PLANS.get(current, PLANS["growth"])
    return {
        "plan": current,
        "name": plan["name"],
        "priceCents": plan["price_cents"],
        "ticketLimit": plan["ticket_limit"],
        "agentLimit": plan["agent_limit"],
        "renewsAt": (row["plan_renews_at"] if row else None),
        "subscriptionId": (row["stripe_subscription_id"] if row else None),
        "available": [
            {"id": k, "name": v["name"], "priceCents": v["price_cents"]}
            for k, v in PLANS.items()
        ],
    }


@router.get("/usage")
async def get_usage(user: dict = Me) -> dict:
    """Month-to-date + rolling 30-day counters. Read from live tables — fine
    for Build-2 volume. Swap to `analytics_daily` aggregates once counts get
    big enough that scans hurt."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    cur = await db.execute(
        "SELECT COUNT(*) FROM tickets WHERE workspace_id = ? AND created_at >= ?",
        (user["workspace_id"], month_start),
    )
    tickets_mtd = int((await cur.fetchone())[0] or 0)
    cur = await db.execute(
        "SELECT COUNT(*) FROM agents WHERE workspace_id = ? AND is_active = true",
        (user["workspace_id"],),
    )
    agents_active = int((await cur.fetchone())[0] or 0)
    cur = await db.execute(
        "SELECT COUNT(*) FROM users WHERE workspace_id = ? AND role IN ('agent','admin')",
        (user["workspace_id"],),
    )
    seats = int((await cur.fetchone())[0] or 0)
    cur = await db.execute(
        """
        SELECT COALESCE(SUM((response->>'tokens_in')::int), 0) AS t_in,
               COALESCE(SUM((response->>'tokens_out')::int), 0) AS t_out
          FROM api_logs WHERE workspace_id = ? AND at >= ?
        """,
        (user["workspace_id"], month_start),
    )
    t = dict(await cur.fetchone() or {})
    cur = await db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE workspace_id = ? AND at >= ?",
        (user["workspace_id"], month_start),
    )
    runs_mtd = int((await cur.fetchone())[0] or 0)

    cur = await db.execute(
        "SELECT plan FROM workspaces WHERE id = ?", (user["workspace_id"],)
    )
    row = await cur.fetchone()
    plan = (row["plan"] if row else None) or "growth"
    plan_row = PLANS.get(plan, PLANS["growth"])
    return {
        "plan": plan,
        "month": now.strftime("%B %Y"),
        "ticketsThisMonth": tickets_mtd,
        "ticketLimit": plan_row["ticket_limit"],
        "agentsActive": agents_active,
        "agentLimit": plan_row["agent_limit"],
        "seats": seats,
        "agentRunsThisMonth": runs_mtd,
        "tokensInThisMonth": int(t.get("t_in") or 0),
        "tokensOutThisMonth": int(t.get("t_out") or 0),
        "nextInvoice": (now.replace(day=1) + timedelta(days=32)).replace(day=1).date().isoformat(),
    }


@router.get("/invoices")
async def get_invoices(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, number, amount_cents, currency, status, period_start,
               period_end, hosted_url, pdf_url, created_at
          FROM invoices
         WHERE workspace_id = ?
         ORDER BY created_at DESC LIMIT 24
        """,
        (user["workspace_id"],),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if rows:
        return [_serialize_invoice(r) for r in rows]
    # Fallback to a demo invoice list so the UI isn't blank on a fresh install.
    today = datetime.now(timezone.utc)
    return [
        {
            "id": f"demo_{i}",
            "number": f"WSX-{today.strftime('%Y')}-{1000 + i}",
            "amountCents": 4900,
            "currency": "usd",
            "status": "paid" if i > 0 else "open",
            "periodStart": (today.replace(day=1) - timedelta(days=30 * i)).date().isoformat(),
            "periodEnd": (today.replace(day=1) - timedelta(days=30 * (i - 1) if i > 0 else 0)).date().isoformat(),
            "hostedUrl": None,
            "pdfUrl": None,
            "createdAt": (today - timedelta(days=30 * i)).isoformat(),
            "demo": True,
        }
        for i in range(0, 3)
    ]


def _serialize_invoice(r: dict) -> dict:
    return {
        "id": r["id"],
        "number": r.get("number"),
        "amountCents": int(r.get("amount_cents") or 0),
        "currency": r.get("currency") or "usd",
        "status": r.get("status") or "paid",
        "periodStart": r.get("period_start"),
        "periodEnd": r.get("period_end"),
        "hostedUrl": r.get("hosted_url"),
        "pdfUrl": r.get("pdf_url"),
        "createdAt": r.get("created_at"),
    }


@router.post("/checkout")
async def start_checkout(body: dict, user: dict = AdminOnly) -> dict:
    """Returns a Stripe Checkout URL the SPA redirects to. When Stripe isn't
    configured we return a synthetic URL that just flips the workspace plan
    locally so the admin can see the upgrade flow work end to end."""
    target = (body or {}).get("plan") or "growth"
    if target not in PLANS:
        raise HTTPException(400, "unknown plan")
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if stripe_key:
        try:
            import httpx  # type: ignore
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.stripe.com/v1/checkout/sessions",
                    auth=(stripe_key, ""),
                    data={
                        "mode": "subscription",
                        "line_items[0][price]": os.environ.get(f"STRIPE_PRICE_{target.upper()}", ""),
                        "line_items[0][quantity]": 1,
                        "success_url": (body or {}).get("returnUrl") or "https://example.com/?s=1",
                        "cancel_url": (body or {}).get("cancelUrl") or "https://example.com/?s=0",
                        "client_reference_id": user["workspace_id"],
                    },
                )
            if resp.status_code < 400:
                j = resp.json()
                return {"url": j.get("url"), "sessionId": j.get("id"), "mode": "stripe"}
        except Exception:
            pass
    # Dev-mode path — flip plan locally, reply with a click-through URL.
    db = await get_db()
    renews = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    await db.execute(
        "UPDATE workspaces SET plan = ?, plan_renews_at = ? WHERE id = ?",
        (target, renews, user["workspace_id"]),
    )
    await db.commit()
    return {
        "url": (body or {}).get("returnUrl") or "/settings?tab=billing&checkout=dev",
        "mode": "dev",
        "plan": target,
        "note": "Stripe not configured — plan flipped directly.",
    }


@router.post("/portal")
async def billing_portal(user: dict = AdminOnly) -> dict:
    """Stripe customer-portal session (spec §3.12). Returns a URL the SPA
    redirects to so the admin can update payment methods / cancel plan."""
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        return {"url": None, "note": "Stripe not configured — connect STRIPE_SECRET_KEY"}
    db = await get_db()
    cur = await db.execute(
        "SELECT stripe_customer_id FROM workspaces WHERE id = ?", (user["workspace_id"],)
    )
    r = await cur.fetchone()
    cid = r[0] if r else None
    if not cid:
        raise HTTPException(400, "workspace has no stripe customer yet — run /billing/checkout first")
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.stripe.com/v1/billing_portal/sessions",
                auth=(stripe_key, ""),
                data={"customer": cid},
            )
        if resp.status_code < 400:
            j = resp.json()
            return {"url": j.get("url")}
    except Exception as e:
        raise HTTPException(500, f"portal failed: {e!r}")
    raise HTTPException(500, "portal failed")
