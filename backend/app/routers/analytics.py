"""Analytics (spec §3.10)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db
from ..deps import Me

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def summary(range: str = Query("7d"), user: dict = Me) -> dict:
    days = int(range[:-1]) if range.endswith("d") and range[:-1].isdigit() else 7
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM tickets WHERE workspace_id = ? "
        "AND created_at > now() - make_interval(days => ?)",
        (user["workspace_id"], days),
    )
    total = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        """
        SELECT COUNT(*) AS resolved,
               AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))) AS avg_seconds
        FROM tickets
        WHERE workspace_id = ? AND status = 'resolved' AND resolved_at IS NOT NULL
        """,
        (user["workspace_id"],),
    )
    row = await cur.fetchone()
    resolved = int(row["resolved"] or 0) if row else 0
    avg_seconds = float(row["avg_seconds"] or 0) if row else 0.0

    # CSAT — mean of `csat_responses.rating` on a 1..5 scale, returned as a %.
    cur = await db.execute(
        """
        SELECT AVG(rating) AS avg_rating, COUNT(*) AS n
          FROM csat_responses
         WHERE workspace_id = ? AND at > now() - make_interval(days => ?)
        """,
        (user["workspace_id"], days),
    )
    cs = await cur.fetchone()
    csat = None
    if cs and cs["n"]:
        csat = round(float(cs["avg_rating"] or 0) * 20, 1)   # 1..5 → 0..100

    # AI deflection — share of resolved tickets with no `agent`/human reply.
    # Counts tickets where every post-create message was from_role in ('ai','system').
    cur = await db.execute(
        """
        WITH resolved AS (
          SELECT id, conversation_id FROM tickets
           WHERE workspace_id = ? AND status = 'resolved'
             AND resolved_at > now() - make_interval(days => ?)
        )
        SELECT
          (SELECT COUNT(*) FROM resolved) AS total,
          (SELECT COUNT(*) FROM resolved r
            WHERE NOT EXISTS (
              SELECT 1 FROM messages m
               WHERE m.conversation_id = r.conversation_id
                 AND m.from_role = 'agent'
            )
          ) AS deflected
        """,
        (user["workspace_id"], days),
    )
    d = await cur.fetchone()
    ai_deflection = None
    if d and d["total"]:
        ai_deflection = round(100.0 * float(d["deflected"] or 0) / float(d["total"] or 1), 1)

    return {
        "ticketsThisWeek": int(total),
        "avgResolution": _format_duration(avg_seconds) if avg_seconds > 0 else None,
        "csat": csat,
        "aiDeflection": ai_deflection,
        "resolved": resolved,
    }


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    rem = m % 60
    return f"{h}h {rem}m" if rem else f"{h}h"


@router.get("/timeseries")
async def timeseries(metric: str = "tickets", range: str = "7d", user: dict = Me) -> dict:
    days = int(range[:-1]) if range.endswith("d") and range[:-1].isdigit() else 7
    days = max(1, min(days, 90))
    db = await get_db()
    if metric == "resolution":
        cur = await db.execute(
            """
            SELECT to_char(date_trunc('day', resolved_at), 'YYYY-MM-DD') AS day,
                   COALESCE(AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))) / 3600, 0) AS value
              FROM tickets
             WHERE workspace_id = ? AND resolved_at IS NOT NULL
               AND resolved_at > now() - make_interval(days => ?)
             GROUP BY 1 ORDER BY 1
            """,
            (user["workspace_id"], days),
        )
    elif metric == "csat":
        cur = await db.execute(
            """
            SELECT to_char(date_trunc('day', at), 'YYYY-MM-DD') AS day,
                   COALESCE(AVG(rating) * 20, 0) AS value
              FROM csat_responses
             WHERE workspace_id = ? AND at > now() - make_interval(days => ?)
             GROUP BY 1 ORDER BY 1
            """,
            (user["workspace_id"], days),
        )
    elif metric == "deflection":
        cur = await db.execute(
            """
            SELECT to_char(date_trunc('day', t.resolved_at), 'YYYY-MM-DD') AS day,
                   COALESCE(100.0 * SUM(CASE WHEN NOT EXISTS (
                     SELECT 1 FROM messages m WHERE m.conversation_id = t.conversation_id AND m.from_role = 'agent'
                   ) THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0), 0) AS value
              FROM tickets t
             WHERE t.workspace_id = ? AND t.resolved_at IS NOT NULL
               AND t.resolved_at > now() - make_interval(days => ?)
             GROUP BY 1 ORDER BY 1
            """,
            (user["workspace_id"], days),
        )
    else:
        cur = await db.execute(
            """
            SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                   COUNT(*) AS value
              FROM tickets
             WHERE workspace_id = ? AND created_at > now() - make_interval(days => ?)
             GROUP BY 1 ORDER BY 1
            """,
            (user["workspace_id"], days),
        )
    series = [{"day": r["day"], "value": float(r["value"] or 0)} for r in [dict(x) for x in await cur.fetchall()]]
    return {"metric": metric, "range": range, "series": series}


# ---------- CSAT collect (spec §3.10) ----------

@router.post("/csat")
async def post_csat(body: dict, user: dict = Me) -> dict:
    """Admin-side testing hook — real surveys land via `GET /csat/:token`.
    Accepts `{ticketId, rating, comment?}`."""
    ticket_id = body.get("ticketId")
    rating = int(body.get("rating") or 0)
    if rating < 1 or rating > 5:
        raise HTTPException(400, "rating must be 1..5")
    comment = body.get("comment")
    db = await get_db()
    cust_id = None
    if ticket_id:
        cur = await db.execute(
            "SELECT customer_id FROM tickets WHERE id = ? AND workspace_id = ?",
            (ticket_id, user["workspace_id"]),
        )
        r = await cur.fetchone()
        cust_id = r[0] if r else None
    await db.execute(
        """
        INSERT INTO csat_responses (workspace_id, ticket_id, customer_id, rating, comment)
        VALUES (?,?,?,?,?)
        """,
        (user["workspace_id"], ticket_id, cust_id, rating, comment),
    )
    await db.commit()
    return {"ok": True, "rating": rating}


# ---------- Public survey (unauth) ----------

# Mounted at root so URLs are shareable: /csat/:token
csat_router = APIRouter(tags=["csat"])


@csat_router.post("/tickets/{ticket_id}/csat/send")
async def issue_csat_token(ticket_id: int, body: dict | None = None, user: dict = Me) -> dict:
    """Agent-triggered: generate a CSAT survey link for a resolved ticket and
    enqueue it for delivery through the outbox (email today, WhatsApp once the
    WA connector ships). Returns the token + public URL — handy for preview
    + letting the operator paste manually if they want."""
    import os
    import secrets
    import json as _json
    db = await get_db()
    cur = await db.execute(
        """
        SELECT t.id, t.customer_id, t.subject, c.email, c.phone, c.wa_id, c.name
          FROM tickets t LEFT JOIN customers c ON c.id = t.customer_id
         WHERE t.id = ? AND t.workspace_id = ?
        """,
        (ticket_id, user["workspace_id"]),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "ticket not found")
    t = dict(row)
    token = secrets.token_urlsafe(24)
    await db.execute(
        """
        INSERT INTO csat_tokens (token, workspace_id, ticket_id, customer_id)
        VALUES (?,?,?,?)
        """,
        (token, user["workspace_id"], ticket_id, t["customer_id"]),
    )
    base = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    public_url = f"{base}/csat/{token}" if base else f"/csat/{token}"
    sent_via = []
    channel_override = (body or {}).get("channel")
    email = t.get("email")
    phone = t.get("phone") or t.get("wa_id")
    # Prefer email when available; fall back to WhatsApp template send.
    if email and channel_override in (None, "email"):
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'email', ?)
            """,
            (user["workspace_id"], _json.dumps({
                "workspace_id": user["workspace_id"],
                "to": email,
                "subject": f"How did we do? Ticket #{ticket_id}",
                "body": (
                    f"Hi {t.get('name') or 'there'},\n\n"
                    f"Thanks for contacting support about \"{t.get('subject') or 'your ticket'}\". "
                    f"We'd love a quick rating:\n\n{public_url}\n\n"
                    f"One click, 30 seconds — it helps us improve.\n"
                ),
                "kind": "csat",
                "csat_token": token,
            })),
        )
        sent_via.append("email")
    elif phone and channel_override in (None, "whatsapp"):
        await db.execute(
            """
            INSERT INTO outbox (workspace_id, kind, payload)
            VALUES (?, 'send_wa', ?)
            """,
            (user["workspace_id"], _json.dumps({
                "workspace_id": user["workspace_id"],
                "to": phone,
                "wa_id": t.get("wa_id") or phone,
                "template": "csat_rating",
                "text": f"How did we do on ticket #{ticket_id}? Rate us: {public_url}",
                "kind": "csat",
                "csat_token": token,
            })),
        )
        sent_via.append("whatsapp")
    await db.commit()
    return {
        "token": token,
        "url": public_url,
        "sentVia": sent_via,
        "delivered": bool(sent_via),
    }


@csat_router.get("/csat/{token}")
async def public_csat_get(token: str) -> dict:
    """Public landing endpoint — returns minimal ticket info so the survey form
    can render. No auth. Returns 410 if already submitted."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT t.token, t.ticket_id, t.submitted_at, tk.subject
          FROM csat_tokens t JOIN tickets tk ON tk.id = t.ticket_id
         WHERE t.token = ?
        """,
        (token,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "survey not found")
    if row["submitted_at"]:
        raise HTTPException(410, "already submitted")
    return {"token": row["token"], "subject": row["subject"]}


@csat_router.post("/csat/{token}")
async def public_csat_post(token: str, body: dict) -> dict:
    rating = int(body.get("rating") or 0)
    if rating < 1 or rating > 5:
        raise HTTPException(400, "rating must be 1..5")
    comment = body.get("comment")
    db = await get_db()
    cur = await db.execute(
        "SELECT workspace_id, ticket_id, customer_id, submitted_at FROM csat_tokens WHERE token = ?",
        (token,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "survey not found")
    if row["submitted_at"]:
        raise HTTPException(410, "already submitted")
    await db.execute(
        """
        INSERT INTO csat_responses (workspace_id, ticket_id, customer_id, rating, comment)
        VALUES (?,?,?,?,?)
        """,
        (row["workspace_id"], row["ticket_id"], row["customer_id"], rating, comment),
    )
    await db.execute(
        "UPDATE csat_tokens SET submitted_at = now() WHERE token = ?",
        (token,),
    )
    await db.commit()
    return {"ok": True, "rating": rating}
