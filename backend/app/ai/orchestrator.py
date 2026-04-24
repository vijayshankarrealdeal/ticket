"""Default orchestrator — built-in `default-orchestrator` agent (spec §3.11.H).

Uses `pydantic-ai` when `OPENAI_API_KEY` is present; otherwise falls back to
a deterministic heuristic responder so the demo works offline. Every run
produces an `agent_runs` row so the Agent Builder page can show history,
tokens, cost, and trace.

Tool surface (wired as thin wrappers over existing routers):
  find_customer, get_customer, list_customer_orders, get_order,
  get_shipping_status, kb_search, similar_tickets,
  create_ticket, update_ticket, create_child_ticket, add_ticket_note,
  send_reply, escalate_to_human, issue_refund, run_library_api.

Hard-coded guardrails (spec §3.11.H "Guardrails"):
  REFUND_CAP_INR = 10_000, LOOP_GUARD_TOOL_CALLS = 8,
  CUSTOMER_RATE_LIMIT = 20/hr, consec-errors = 3.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

from ..db import get_db, jl, js

log = logging.getLogger(__name__)

REFUND_CAP_INR = 10_000
LOOP_GUARD_TOOL_CALLS = 8
CONSEC_ERROR_LIMIT = 3
CUSTOMER_RATE_LIMIT = 20  # per-customer per-hour


@dataclass
class AgentCtx:
    workspace_id: str
    conversation_id: str | None
    customer_id: str | None
    channel: str | None
    actor_user_id: str | None
    trace_id: str
    # Agent selection (inbound pipeline picks the workspace's preferred agent;
    # `agent_id` ends up on the saved `messages.ai_agent_id` row so Conversations
    # can show who drafted which reply).
    agent_id: str | None = None
    agent_name: str | None = None
    # When true, callers drive the chunked fan-out themselves — `send_reply`
    # skips the typing indicator but still persists + fans out the final row
    # so the SPA reconciles the streaming bubble with the saved message.
    stream: bool = False
    # Guardrail counters — reset every run.
    tool_calls: int = 0
    consecutive_errors: int = 0
    steps: list[dict] = field(default_factory=list)


# ---------- guardrails -------------------------------------------------------

class GuardrailHit(Exception):
    """Raised when a guardrail (loop / rate-limit / consec-error) trips."""


async def _rate_limit_check(ctx: AgentCtx) -> None:
    """Block if this customer has hit the per-hour run ceiling. Silent queue-
    and-escalate is handled by the caller — we just raise here."""
    if not ctx.customer_id:
        return
    db = await get_db()
    cur = await db.execute(
        """
        SELECT COUNT(*) FROM agent_runs
         WHERE workspace_id = ?
           AND conversation_id IN (
             SELECT id FROM conversations WHERE customer_id = ?
           )
           AND at > now() - interval '1 hour'
        """,
        (ctx.workspace_id, ctx.customer_id),
    )
    row = await cur.fetchone()
    count = int((row or [0])[0] or 0)
    if count >= CUSTOMER_RATE_LIMIT:
        raise GuardrailHit(f"rate_limit:{count}/{CUSTOMER_RATE_LIMIT}")


def _guard_tool(tool_name: str):
    """Decorator that stamps each tool invocation into ctx.steps and enforces
    loop / consecutive-error limits. Keeps the guardrails enforceable even when
    the LLM is off the rails."""
    def wrap(fn: Callable[..., Awaitable[Any]]):
        async def inner(ctx: AgentCtx, *args, **kwargs):
            ctx.tool_calls += 1
            if ctx.tool_calls > LOOP_GUARD_TOOL_CALLS:
                raise GuardrailHit(f"loop_guard:{ctx.tool_calls}")
            step: dict[str, Any] = {"tool": tool_name, "args": _safe(kwargs)}
            try:
                result = await fn(ctx, *args, **kwargs)
                ctx.consecutive_errors = 0
                step["ok"] = True
                return result
            except GuardrailHit:
                raise
            except Exception as e:
                ctx.consecutive_errors += 1
                step["ok"] = False
                step["error"] = repr(e)[:200]
                if ctx.consecutive_errors >= CONSEC_ERROR_LIMIT:
                    raise GuardrailHit(f"consec_errors:{ctx.consecutive_errors}")
                raise
            finally:
                ctx.steps.append(step)
        return inner
    return wrap


def _safe(obj: Any) -> Any:
    """Scrub PII-ish fields before writing a step into ctx.steps.trace."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in {"phone", "email", "address", "wa_id", "psid", "password"}:
                out[k] = "<redacted>"
            else:
                out[k] = _safe(v)
        return out
    if isinstance(obj, list):
        return [_safe(x) for x in obj]
    return obj


# ---------- tool implementations --------------------------------------------

@_guard_tool("find_customer")
async def tool_find_customer(ctx: AgentCtx, phone: str | None = None, email: str | None = None,
                             name: str | None = None) -> dict | None:
    db = await get_db()
    clauses = ["workspace_id = ?"]
    params: list = [ctx.workspace_id]
    if phone:
        clauses.append("phone = ?"); params.append(phone)
    if email:
        clauses.append("email = ?"); params.append(email.lower())
    if name:
        clauses.append("name ILIKE ?"); params.append(f"%{name}%")
    if len(clauses) == 1:
        return None
    cur = await db.execute(f"SELECT * FROM customers WHERE {' AND '.join(clauses)} LIMIT 1", params)
    row = await cur.fetchone()
    return dict(row) if row else None


@_guard_tool("get_customer")
async def tool_get_customer(ctx: AgentCtx, customer_id: str | None = None) -> dict | None:
    cid = customer_id or ctx.customer_id
    if not cid:
        return None
    db = await get_db()
    cur = await db.execute("SELECT * FROM customers WHERE id = ? AND workspace_id = ?",
                           (cid, ctx.workspace_id))
    row = await cur.fetchone()
    return dict(row) if row else None


@_guard_tool("get_order")
async def tool_get_order(ctx: AgentCtx, order_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM orders WHERE id = ? AND workspace_id = ?", (order_id, ctx.workspace_id)
    )
    row = await cur.fetchone()
    if not row:
        return None
    o = dict(row)
    return {
        "id": o["id"], "status": o["status"], "total": o["total"],
        "placedAt": o.get("placed_at"), "items": jl(o.get("items"), []),
    }


@_guard_tool("list_customer_orders")
async def tool_list_customer_orders(ctx: AgentCtx, customer_id: str | None = None,
                                    limit: int = 5) -> list[dict]:
    cid = customer_id or ctx.customer_id
    if not cid:
        return []
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT ?",
        (cid, max(1, min(limit, 20))),
    )
    return [
        {"id": r["id"], "status": r["status"], "total": r["total"], "placedAt": r.get("placed_at")}
        for r in [dict(x) for x in await cur.fetchall()]
    ]


@_guard_tool("get_shipping_status")
async def tool_get_shipping_status(ctx: AgentCtx, order_id: str) -> dict:
    """Synthesises a shipping status from the order row. Real courier adapters
    (Clickpost/Shiprocket) plug in here via the API Library."""
    db = await get_db()
    cur = await db.execute("SELECT * FROM orders WHERE id = ? AND workspace_id = ?",
                           (order_id, ctx.workspace_id))
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": f"order {order_id} not found"}
    o = dict(row)
    return {
        "ok": True, "orderId": o["id"], "status": o["status"],
        "placedAt": o.get("placed_at"),
        "courier": "—", "tracking": f"WX-{order_id[-6:].upper()}",
    }


@_guard_tool("kb_search")
async def tool_kb_search(ctx: AgentCtx, query: str, k: int = 5) -> list[dict]:
    """Knowledge base search. When chunks have an `embedding` we cosine-rank
    a candidate set in Python; otherwise fall back to trigram/ILIKE so the
    tool keeps working on a fresh install. Once pgvector + HNSW land this
    becomes a single `ORDER BY embedding <=> $1` query — interface unchanged."""
    from .embeddings import embed as _embed, decode as _decode, cosine as _cos

    db = await get_db()
    k = max(1, min(k, 10))
    # Try embedding-ranked path first (60-row candidate window keeps it cheap).
    cur = await db.execute(
        """
        SELECT id, title, body, source, embedding FROM kb_chunks
         WHERE workspace_id = ? AND embedding IS NOT NULL
         ORDER BY created_at DESC LIMIT 60
        """,
        (ctx.workspace_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if rows:
        qvec = _decode(_embed(query))
        scored = []
        for r in rows:
            score = _cos(qvec, _decode(r.get("embedding")))
            scored.append((score, r))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {"id": r["id"], "title": r.get("title"), "body": r["body"],
             "source": r.get("source"), "score": round(s, 3)}
            for s, r in scored[:k]
        ]
    # Fallback: ILIKE on body/title — same interface, no embedding needed.
    cur = await db.execute(
        """
        SELECT id, title, body, source
          FROM kb_chunks
         WHERE workspace_id = ?
           AND (body ILIKE ? OR title ILIKE ?)
         LIMIT ?
        """,
        (ctx.workspace_id, f"%{query}%", f"%{query}%", k),
    )
    return [dict(r) for r in await cur.fetchall()]


@_guard_tool("similar_tickets")
async def tool_similar_tickets(ctx: AgentCtx, subject: str) -> list[dict]:
    """Find similar tickets by cosine over `subject_embedding` when present;
    otherwise fall back to ILIKE."""
    from .embeddings import embed as _embed, decode as _decode, cosine as _cos

    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, subject, status, task, subject_embedding FROM tickets
         WHERE workspace_id = ? AND subject_embedding IS NOT NULL
         ORDER BY created_at DESC LIMIT 100
        """,
        (ctx.workspace_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if rows:
        qvec = _decode(_embed(subject))
        scored = sorted(
            ((_cos(qvec, _decode(r.get("subject_embedding"))), r) for r in rows),
            key=lambda t: t[0], reverse=True,
        )
        return [
            {"id": r["id"], "subject": r["subject"], "status": r["status"],
             "task": r["task"], "score": round(s, 3)}
            for s, r in scored[:5] if s > 0.05
        ]
    cur = await db.execute(
        """
        SELECT id, subject, status, task FROM tickets
         WHERE workspace_id = ? AND (subject ILIKE ? OR task ILIKE ?)
         ORDER BY created_at DESC LIMIT 5
        """,
        (ctx.workspace_id, f"%{subject}%", f"%{subject}%"),
    )
    return [dict(r) for r in await cur.fetchall()]


@_guard_tool("create_ticket")
async def tool_create_ticket(ctx: AgentCtx, subject: str, task: str = "general_enquiry",
                             team: str = "Support", priority: str = "normal",
                             description: str | None = None) -> dict:
    if not ctx.customer_id:
        return {"ok": False, "error": "no customer context"}
    db = await get_db()
    sla_due = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    cur = await db.execute(
        """
        INSERT INTO tickets (workspace_id, customer_id, conversation_id, source, channel,
                             task, team, priority, subject, description,
                             tags, custom_fields, status, sla_due_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'in_progress', ?)
        RETURNING id
        """,
        (ctx.workspace_id, ctx.customer_id, ctx.conversation_id,
         ctx.channel or "whatsapp", ctx.channel or "whatsapp",
         task, team, priority, subject, description or subject,
         js([]), js({}), sla_due),
    )
    tid = cur.lastrowid
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'system', ?)",
        (tid, f"Ticket created by AI — task: {task}"),
    )
    await db.commit()
    from ..ws import broker
    await broker.publish(f"inbox:{ctx.workspace_id}", "ticket.created",
                         {"id": f"#{tid}", "subject": subject, "task": task})
    return {"ok": True, "ticketId": tid}


@_guard_tool("update_ticket")
async def tool_update_ticket(ctx: AgentCtx, ticket_id: int, status: str | None = None,
                             team: str | None = None, voc: str | None = None,
                             resolution_notes: str | None = None) -> dict:
    db = await get_db()
    updates: dict = {}
    timeline: list[str] = []
    if status:
        updates["status"] = status
        timeline.append(f"Status → {status}")
    if team:
        updates["team"] = team
        timeline.append(f"Team → {team}")
    if voc:
        updates["voc"] = voc
    if resolution_notes:
        updates["resolution_notes"] = resolution_notes
    if not updates:
        return {"ok": False, "error": "no fields to update"}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in updates)
    await db.execute(
        f"UPDATE tickets SET {sets} WHERE id = ? AND workspace_id = ?",
        (*updates.values(), ticket_id, ctx.workspace_id),
    )
    for text in timeline:
        await db.execute(
            "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'ai', ?)",
            (ticket_id, text),
        )
    await db.commit()
    from ..ws import broker
    await broker.publish(f"ticket:{ticket_id}", "ticket.updated", {"id": f"#{ticket_id}", **updates})
    return {"ok": True, "ticketId": ticket_id}


@_guard_tool("create_child_ticket")
async def tool_create_child_ticket(ctx: AgentCtx, parent_ticket_id: int, objective: str,
                                   team: str = "Logistics", prompt: str | None = None,
                                   assignee_email: str | None = None) -> dict:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO ticket_child (ticket_id, assignee_email, team, objective, prompt)
        VALUES (?,?,?,?,?)
        ON CONFLICT (ticket_id) DO UPDATE SET
          assignee_email = EXCLUDED.assignee_email,
          team           = EXCLUDED.team,
          objective      = EXCLUDED.objective,
          prompt         = EXCLUDED.prompt
        """,
        (parent_ticket_id, assignee_email, team, objective, prompt or objective),
    )
    await db.execute(
        "UPDATE tickets SET status = 'blocked' WHERE id = ? AND workspace_id = ?",
        (parent_ticket_id, ctx.workspace_id),
    )
    await db.execute(
        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'ai', ?)",
        (parent_ticket_id, f"Child ticket opened: {objective}"),
    )
    await db.commit()
    return {"ok": True, "ticketId": parent_ticket_id, "objective": objective}


@_guard_tool("add_ticket_note")
async def tool_add_ticket_note(ctx: AgentCtx, ticket_id: int, text: str) -> dict:
    db = await get_db()
    await db.execute(
        "INSERT INTO ticket_notes (ticket_id, author, text) VALUES (?, 'ai', ?)",
        (ticket_id, text),
    )
    await db.commit()
    return {"ok": True, "ticketId": ticket_id}


@_guard_tool("send_reply")
async def tool_send_reply(ctx: AgentCtx, text: str) -> dict:
    """Persist the reply on the conversation + queue it to the outbox for the
    real adapter. Without a conversation context (test mode) we just echo.

    When `ctx.stream=True` the caller is driving its own chunked fan-out via
    `stream_default_orchestrator`, so we persist + fan out `message.created`
    with `{stream_final:True}` so the SPA can swap the streaming bubble into
    the saved row instead of rendering a second one."""
    if not ctx.conversation_id:
        return {"ok": True, "echoed": text}
    db = await get_db()
    mid = str(uuid.uuid4())
    agent_name = ctx.agent_name or "default-orchestrator"
    await db.execute(
        """
        INSERT INTO messages (id, workspace_id, conversation_id, from_role, from_name,
                              text, attachments, ai_agent_id)
        VALUES (?, ?, ?, 'ai', ?, ?, '[]'::jsonb, ?)
        """,
        (mid, ctx.workspace_id, ctx.conversation_id, agent_name, text, ctx.agent_id),
    )
    await db.execute(
        "UPDATE conversations SET last_message_at = now(), preview = ? WHERE id = ?",
        (text[:160], ctx.conversation_id),
    )
    # Build a channel-aware outbox payload. The outbox adapters
    # (`_send_gmail` / `_send_wa` / …) read `to`/`email`/`phone`/`wa_id` so
    # we need to resolve the customer's contact info from the conversation.
    channel = (ctx.channel or "generic").lower()
    kind = f"send_{channel}"
    cur = await db.execute(
        """
        SELECT cu.email, cu.phone, cu.wa_id, cu.psid, cu.name,
               c.external_thread_id, c.subject AS conv_subject,
               (SELECT text FROM messages
                 WHERE conversation_id = c.id AND from_role = 'customer'
                 ORDER BY at DESC LIMIT 1) AS last_inbound_text,
               (SELECT external_message_id FROM messages
                 WHERE conversation_id = c.id AND from_role = 'customer'
                 ORDER BY at DESC LIMIT 1) AS last_inbound_ext
          FROM conversations c LEFT JOIN customers cu ON cu.id = c.customer_id
         WHERE c.id = ?
        """,
        (ctx.conversation_id,),
    )
    row = dict(await cur.fetchone() or {})
    # Subject for email replies: Gmail threading requires the reply subject
    # to match the thread's subject (ignoring any Re:/Fwd: prefix). Prefer
    # the conversation's stored subject (set by the poller from the original
    # Subject header); fall back to first line of the inbound body.
    subject = None
    conv_subject = (row.get("conv_subject") or "").strip()
    if conv_subject:
        subject = conv_subject if conv_subject.lower().startswith("re:") else f"Re: {conv_subject}"
    elif row.get("last_inbound_text"):
        first_line = (row["last_inbound_text"].splitlines() or [""])[0].strip()[:120]
        subject = f"Re: {first_line}" if first_line else "Re: your message"
    payload = {
        "workspace_id": ctx.workspace_id,
        "conversationId": ctx.conversation_id,
        "messageId": mid,
        "text": text,
        "body": text,
        "to": row.get("email") or row.get("phone") or row.get("wa_id"),
        "email": row.get("email"),
        "phone": row.get("phone"),
        "wa_id": row.get("wa_id"),
        "psid": row.get("psid"),
        "subject": subject,
        "thread_id": row.get("external_thread_id"),
        "in_reply_to": row.get("last_inbound_ext"),
    }
    await db.execute(
        "INSERT INTO outbox (workspace_id, kind, payload) VALUES (?, ?, ?)",
        (ctx.workspace_id, kind, js(payload)),
    )
    # AI just sent the reply — flip any live ticket on this conversation to
    # `waiting_for_customer` so agent queues aren't polluted by tickets the
    # AI is already handling. Skip blocked/resolved/closed (human-owned).
    cur = await db.execute(
        """
        UPDATE tickets SET status = 'waiting_for_customer', updated_at = now()
         WHERE conversation_id = ? AND workspace_id = ?
           AND status IN ('queued','in_progress','assigned')
         RETURNING id
        """,
        (ctx.conversation_id, ctx.workspace_id),
    )
    touched = [r[0] for r in await cur.fetchall()]
    await db.commit()
    # Fan out so the conversation pane animates the bubble.
    from ..ws import broker
    for tid in touched:
        await broker.publish(
            f"ticket:{tid}", "ticket.updated",
            {"id": f"#{tid}", "status": "waiting_for_customer"},
        )
        await broker.publish(
            f"inbox:{ctx.workspace_id}", "ticket.updated",
            {"id": f"#{tid}", "status": "waiting_for_customer"},
        )
    payload = {
        "id": mid, "from": "me", "text": text, "at": None,
        "fromName": agent_name, "delivered": True,
        "aiAgentId": ctx.agent_id,
    }
    if ctx.stream:
        payload["streamFinal"] = True
    await broker.publish(f"conv:{ctx.conversation_id}", "message.created", payload)
    return {"ok": True, "messageId": mid}


@_guard_tool("issue_refund")
async def tool_issue_refund(ctx: AgentCtx, order_id: str, amount: float, reason: str) -> dict:
    if amount > REFUND_CAP_INR:
        return {"ok": False, "error": f"refund capped at ₹{REFUND_CAP_INR}; escalate_to_human"}
    # Real adapter would call the payment processor via the outbox. Record it.
    db = await get_db()
    await db.execute(
        "INSERT INTO outbox (workspace_id, kind, payload) VALUES (?, 'send_refund', ?)",
        (ctx.workspace_id, js({"orderId": order_id, "amount": amount, "reason": reason})),
    )
    await db.commit()
    return {"ok": True, "orderId": order_id, "amount": amount}


@_guard_tool("escalate_to_human")
async def tool_escalate_to_human(ctx: AgentCtx, reason: str, team: str = "Support") -> dict:
    from ..routers.notifications import create_notification
    db = await get_db()
    # Prefer an admin (they own the queue and route to team members). Fall back
    # to any agent only if there is no admin in the workspace.
    cur = await db.execute(
        "SELECT id FROM users WHERE workspace_id = ? AND role = 'admin' "
        "ORDER BY last_seen_at DESC NULLS LAST LIMIT 1",
        (ctx.workspace_id,),
    )
    r = await cur.fetchone()
    if not r:
        cur = await db.execute(
            "SELECT id FROM users WHERE workspace_id = ? AND role IN ('admin','agent') LIMIT 1",
            (ctx.workspace_id,),
        )
        r = await cur.fetchone()
    admin_id = r[0] if r else None
    if admin_id:
        await create_notification(
            admin_id, "assign",
            title="AI escalated a conversation",
            body=reason,
            link=f"/conversations?id={ctx.conversation_id}" if ctx.conversation_id else "/conversations",
        )
    # Update linked ticket: escalate status + hand the ticket to the admin so
    # it leaves the "Unassigned" queue and lands in the admin's lap. The admin
    # can then reassign to the right team member.
    if ctx.conversation_id:
        if admin_id:
            await db.execute(
                """
                UPDATE tickets SET status = 'blocked', assignee_id = ?, team = ?, updated_at = now()
                 WHERE conversation_id = ? AND status NOT IN ('resolved','closed')
                """,
                (admin_id, team or "Support", ctx.conversation_id),
            )
            cur = await db.execute(
                "SELECT id FROM tickets WHERE conversation_id = ? AND status NOT IN ('resolved','closed') LIMIT 1",
                (ctx.conversation_id,),
            )
            trow = await cur.fetchone()
            if trow:
                await db.execute(
                    "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'ai', ?)",
                    (trow[0], f"AI escalated to admin — {reason}"),
                )
        else:
            await db.execute(
                """
                UPDATE tickets SET status = 'blocked', updated_at = now()
                 WHERE conversation_id = ? AND status NOT IN ('resolved','closed')
                """,
                (ctx.conversation_id,),
            )
        await db.commit()
    return {"ok": True, "team": team, "reason": reason, "assignedTo": admin_id}


@_guard_tool("run_library_api")
async def tool_run_library_api(ctx: AgentCtx, name: str, args: dict | None = None) -> dict:
    """Catch-all bridge to the API Library. Every call lands in `api_logs` so
    Channels > API Logs can trace it."""
    from ..routers.channels import _execute_api
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM apis WHERE workspace_id = ? AND name = ? LIMIT 1",
        (ctx.workspace_id, name),
    )
    row = await cur.fetchone()
    if not row:
        return {"ok": False, "error": f"api '{name}' not registered"}
    return await _execute_api(db, ctx.workspace_id, dict(row), args or {}, ctx.customer_id)


# ---------- heuristic fallback runner ----------------------------------------

async def _fallback_run(ctx: AgentCtx, user_message: str) -> dict:
    """Deterministic responder used when OPENAI_API_KEY is absent. Runs a few
    real tools so the `api_logs` + `agent_runs` trails are still exercised."""
    # Customer identification
    if ctx.customer_id:
        try:
            await tool_get_customer(ctx, ctx.customer_id)
        except Exception:
            pass

    lower = (user_message or "").lower()
    if "refund" in lower:
        text = "I can help with a refund. Could you share your order ID? I'll check the eligibility window."
    elif "delivery" in lower or "tracking" in lower or "where" in lower:
        orders: list[dict] = []
        if ctx.customer_id:
            try:
                orders = await tool_list_customer_orders(ctx, customer_id=ctx.customer_id, limit=1)
            except Exception:
                orders = []
        if orders:
            text = f"Your most recent order {orders[0]['id']} is currently **{orders[0]['status']}**. I'll keep you posted if anything changes."
        else:
            text = "Happy to check — could you share the order number so I can pull up the shipping status?"
    elif "return" in lower:
        text = "Got it — returns are accepted within 14 days of delivery. Want me to start a pickup?"
    else:
        text = "Thanks for reaching out — I'll loop in the right teammate to help you with this."

    if ctx.conversation_id:
        try:
            await tool_send_reply(ctx, text)
        except GuardrailHit:
            raise
        except Exception:
            pass
    return {"output": text, "steps": ctx.steps}


# ---------- pydantic-ai runner (lazy import) ---------------------------------

async def _pydantic_ai_run(ctx: AgentCtx, user_message: str, model: str) -> dict:
    from pydantic_ai import Agent, RunContext  # type: ignore

    # Pull workspace profile up front so the system prompt can carry product
    # context. Workspace owners inject services/policies/brand voice via
    # `workspaces.ai_prompt_append`.
    ws_name = "the business"
    ws_profile = ""
    try:
        db = await get_db()
        cur = await db.execute(
            "SELECT name, ai_prompt_append FROM workspaces WHERE id = ?",
            (ctx.workspace_id,),
        )
        wrow = await cur.fetchone()
        if wrow:
            ws_name = wrow["name"] or ws_name
            ws_profile = (wrow["ai_prompt_append"] or "").strip()
    except Exception:
        pass

    agent = Agent(
        model=f"openai:{model}",
        deps_type=AgentCtx,
        retries=2,
        system_prompt=(
            f"You are {ws_name}'s AI support agent and you own the ticket end-to-end. "
            "Always call tools rather than inventing data. Use `kb_search` to ground product / "
            "policy answers; use `get_order` / `get_shipping_status` for order questions; "
            "answer directly with `send_reply` when you have the information. "
            "When you have fully answered the customer's question and no further action is "
            "pending on our side, call `update_ticket(status='resolved', resolution_notes=...)` "
            "to close the ticket — don't leave it parked on a human queue. "
            "Only call `escalate_to_human` when the request truly needs real-world action you "
            "can't take (refund above cap, legal, account change) or the profile/KB lacks the answer. "
            f"Never issue a refund above ₹{REFUND_CAP_INR}. Never call more than "
            f"{LOOP_GUARD_TOOL_CALLS} tools in a single turn.\n\n"
            f"Business profile — {ws_name}:\n"
            f"{ws_profile or '(no profile — rely on KB + conversation context)'}"
        ),
    )

    @agent.tool
    async def find_customer(_ctx: RunContext[AgentCtx], phone: str | None = None,
                            email: str | None = None, name: str | None = None) -> dict | None:
        return await tool_find_customer(ctx, phone, email, name)

    @agent.tool
    async def get_order(_ctx: RunContext[AgentCtx], order_id: str) -> dict | None:
        return await tool_get_order(ctx, order_id)

    @agent.tool
    async def list_customer_orders(_ctx: RunContext[AgentCtx], limit: int = 5) -> list[dict]:
        return await tool_list_customer_orders(ctx, ctx.customer_id, limit)

    @agent.tool
    async def get_shipping_status(_ctx: RunContext[AgentCtx], order_id: str) -> dict:
        return await tool_get_shipping_status(ctx, order_id)

    @agent.tool
    async def kb_search(_ctx: RunContext[AgentCtx], query: str, k: int = 5) -> list[dict]:
        return await tool_kb_search(ctx, query, k)

    @agent.tool
    async def similar_tickets(_ctx: RunContext[AgentCtx], subject: str) -> list[dict]:
        return await tool_similar_tickets(ctx, subject)

    @agent.tool
    async def create_ticket(_ctx: RunContext[AgentCtx], subject: str,
                            task: str = "general_enquiry", team: str = "Support") -> dict:
        return await tool_create_ticket(ctx, subject, task, team)

    @agent.tool
    async def update_ticket(_ctx: RunContext[AgentCtx], ticket_id: int,
                            status: str | None = None, team: str | None = None,
                            voc: str | None = None, resolution_notes: str | None = None) -> dict:
        return await tool_update_ticket(ctx, ticket_id, status, team, voc, resolution_notes)

    @agent.tool
    async def create_child_ticket(_ctx: RunContext[AgentCtx], parent_ticket_id: int,
                                  objective: str, team: str = "Logistics") -> dict:
        return await tool_create_child_ticket(ctx, parent_ticket_id, objective, team)

    @agent.tool
    async def add_ticket_note(_ctx: RunContext[AgentCtx], ticket_id: int, text: str) -> dict:
        return await tool_add_ticket_note(ctx, ticket_id, text)

    @agent.tool
    async def send_reply(_ctx: RunContext[AgentCtx], text: str) -> dict:
        return await tool_send_reply(ctx, text)

    @agent.tool
    async def issue_refund(_ctx: RunContext[AgentCtx], order_id: str, amount: float, reason: str) -> dict:
        return await tool_issue_refund(ctx, order_id, amount, reason)

    @agent.tool
    async def escalate_to_human(_ctx: RunContext[AgentCtx], reason: str, team: str = "Support") -> dict:
        return await tool_escalate_to_human(ctx, reason, team)

    @agent.tool
    async def run_library_api(_ctx: RunContext[AgentCtx], name: str, args: dict | None = None) -> dict:
        return await tool_run_library_api(ctx, name, args)

    result = await agent.run(user_message, deps=ctx)
    return {"output": str(result.data), "steps": ctx.steps}


# ---------- Azure OpenAI runner ---------------------------------------------

def _azure_oai_creds() -> tuple[str | None, str | None, str, str]:
    """Resolve Azure OpenAI env vars with a few alternate spellings. Returns
    `(key, endpoint, api_version, deployment)`. Key is the auth header; the
    endpoint + deployment + api_version go into the URL."""
    key = (os.environ.get("AZURE_OPENAI_API_KEY")
           or os.environ.get("AZURE_OPEN_AI_KEY")
           or os.environ.get("AZURE_OPENAI_KEY"))
    endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT")
                or os.environ.get("AZURE_ENDPOINT") or "").rstrip("/")
    api_version = (os.environ.get("AZURE_OPENAI_API_VERSION")
                   or os.environ.get("API_VERSION") or "2024-12-01-preview")
    deployment = (os.environ.get("AZURE_OPENAI_DEPLOYMENT")
                  or os.environ.get("AZURE_DEPLOYMENT") or "gpt-4o-mini")
    return key, endpoint or None, api_version, deployment


async def _load_conversation_context(ctx: AgentCtx, query: str | None = None) -> dict:
    """Pull the recent message history + customer / order context + workspace
    business profile + relevant KB chunks so the LLM can answer with grounded,
    product-specific information instead of a canned response."""
    db = await get_db()
    out: dict = {"customer": None, "orders": [], "history": [],
                 "workspace": None, "kb": []}
    if ctx.customer_id:
        try:
            out["customer"] = await tool_get_customer(ctx, ctx.customer_id)
        except Exception:
            pass
        try:
            out["orders"] = await tool_list_customer_orders(ctx, ctx.customer_id, limit=3)
        except Exception:
            pass
    if ctx.conversation_id:
        cur = await db.execute(
            """
            SELECT from_role, from_name, text, at FROM messages
             WHERE conversation_id = ? ORDER BY at DESC LIMIT 8
            """,
            (ctx.conversation_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        # Oldest-first for the LLM.
        out["history"] = list(reversed(rows))
    # Workspace business profile — owners put product/services/policy info in
    # `ai_prompt_append`; it gets concatenated into the system prompt so the
    # LLM has product-specific grounding beyond what a KB chunk carries.
    try:
        cur = await db.execute(
            "SELECT name, ai_prompt_append FROM workspaces WHERE id = ?",
            (ctx.workspace_id,),
        )
        wrow = await cur.fetchone()
        if wrow:
            out["workspace"] = {"name": wrow["name"],
                                "ai_prompt_append": (wrow["ai_prompt_append"] or "").strip()}
    except Exception:
        pass
    # Relevant KB — best-effort trigram/ILIKE (the full embedding path lives in
    # `tool_kb_search`; for tool-less flows we inline the cheap fallback so KB
    # content still shows up in the prompt).
    if query:
        try:
            cur = await db.execute(
                """
                SELECT title, body FROM kb_chunks
                 WHERE workspace_id = ?
                   AND (body ILIKE ? OR title ILIKE ?)
                 LIMIT 6
                """,
                (ctx.workspace_id, f"%{query[:80]}%", f"%{query[:80]}%"),
            )
            out["kb"] = [dict(r) for r in await cur.fetchall()]
        except Exception:
            pass
    return out


async def _azure_openai_run(ctx: AgentCtx, user_message: str, deployment: str) -> dict:
    """Direct Azure OpenAI chat completion — no pydantic-ai, no tool-calling.
    We hand the LLM the conversation history + customer context + workspace
    profile + KB, and ask it for a structured `{reply, outcome}` so we can
    auto-resolve or escalate based on the AI's own judgement (AI owns the
    ticket end-to-end, spec §3.11.H)."""
    key, endpoint, api_version, default_deployment = _azure_oai_creds()
    if not key or not endpoint:
        raise RuntimeError("azure openai creds missing")
    deployment = deployment or default_deployment
    context = await _load_conversation_context(ctx, query=user_message)
    history_text = "\n".join(
        f"{m['from_role']}: {(m.get('text') or '').strip()[:500]}"
        for m in (context.get("history") or [])
    ) or "(no prior messages)"
    customer = context.get("customer") or {}
    orders = context.get("orders") or []
    orders_block = "\n".join(
        f"- order {o.get('id')}: status={o.get('status')} total=₹{o.get('total')}"
        for o in orders
    ) or "  (no recent orders)"
    workspace = context.get("workspace") or {}
    ws_name = workspace.get("name") or "the business"
    ws_profile = workspace.get("ai_prompt_append") or ""
    kb_rows = context.get("kb") or []
    kb_block = "\n".join(
        f"- {(r.get('title') or 'note').strip()}: {(r.get('body') or '').strip()[:400]}"
        for r in kb_rows
    ) or "  (no relevant knowledge-base entries found)"

    system_prompt = (
        f"You are {ws_name}'s AI support agent and you own this ticket end-to-end. "
        "Read the customer's latest message and respond directly, helpfully, and concisely, "
        "grounding your answer in the Business profile and Knowledge base provided below. "
        "If the Business profile / KB has the answer, give it plainly — do not say 'I'll check with the team'. "
        "Only defer to a human when the request needs real-world action you cannot take (refunds above cap, "
        "account changes, legal, or the question truly isn't covered by the profile/KB). "
        "Keep it under 4 short sentences. No salutations — the customer already knows they're talking to support. "
        f"Never promise a refund over ₹{REFUND_CAP_INR} on your own authority.\n\n"
        f"Business profile — {ws_name}:\n"
        f"{ws_profile or '  (no profile provided — rely on KB + conversation context)'}\n\n"
        "After drafting the reply, decide the ticket outcome:\n"
        "  - 'resolved': you fully answered; the customer's question is closed and no further action is pending on our side.\n"
        "  - 'waiting_for_customer': you replied with info or a follow-up question and are waiting on their response.\n"
        "  - 'needs_human': the request is outside what you can handle (refund > cap, legal, account change, missing info in profile/KB).\n\n"
        "Respond with JSON only: {\"reply\": \"...\", \"outcome\": \"resolved|waiting_for_customer|needs_human\", \"reason\": \"...\"}."
    )
    user_prompt = (
        f"Customer profile:\n"
        f"- name: {customer.get('name') or 'unknown'}\n"
        f"- email: {customer.get('email') or 'unknown'}\n"
        f"- phone: {customer.get('phone') or 'unknown'}\n"
        f"- orders:\n{orders_block}\n\n"
        f"Knowledge base (most relevant):\n{kb_block}\n\n"
        f"Recent conversation (oldest first):\n{history_text}\n\n"
        f"Customer's latest message:\n{user_message}\n\n"
        f"Draft the JSON response now."
    )

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    try:
        import httpx  # type: ignore
    except ImportError:
        raise RuntimeError("httpx required for Azure OpenAI")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"api-key": key, "Content-Type": "application/json"},
            json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 500,
                "response_format": {"type": "json_object"},
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"azure openai {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = ""
    outcome = "waiting_for_customer"
    reason = ""
    try:
        parsed = json.loads(raw)
        text = (parsed.get("reply") or "").strip()
        outcome = (parsed.get("outcome") or "waiting_for_customer").strip()
        reason = (parsed.get("reason") or "").strip()
    except Exception:
        # Structured-output parse failed — treat the raw text as the reply and
        # keep the safe default outcome (waiting_for_customer).
        text = raw.strip()
    if outcome not in {"resolved", "waiting_for_customer", "needs_human"}:
        outcome = "waiting_for_customer"
    if not text:
        text = "Thanks — let me check on that and get back to you."
    if ctx.conversation_id:
        try:
            await tool_send_reply(ctx, text)
        except GuardrailHit:
            raise
        except Exception:
            log.exception("send_reply failed after azure generation")
        # Apply the AI's own judgement on ticket state. `send_reply` already
        # set it to `waiting_for_customer` — upgrade to `resolved` when the
        # AI is confident the question is fully answered, or hand off to a
        # human when the AI flagged `needs_human`.
        try:
            if outcome == "resolved":
                db = await get_db()
                cur = await db.execute(
                    """
                    UPDATE tickets SET status = 'resolved', resolved_at = now(),
                                       resolution_notes = ?, updated_at = now()
                     WHERE conversation_id = ? AND workspace_id = ?
                       AND status NOT IN ('resolved','closed')
                     RETURNING id
                    """,
                    (reason or "AI resolved — customer's question fully answered",
                     ctx.conversation_id, ctx.workspace_id),
                )
                tids = [r[0] for r in await cur.fetchall()]
                for tid in tids:
                    await db.execute(
                        "INSERT INTO ticket_timeline (ticket_id, actor, text) VALUES (?, 'ai', ?)",
                        (tid, f"AI marked resolved — {reason or 'question fully answered'}"),
                    )
                await db.commit()
                from ..ws import broker
                for tid in tids:
                    await broker.publish(
                        f"ticket:{tid}", "ticket.updated",
                        {"id": f"#{tid}", "status": "resolved"},
                    )
                    await broker.publish(
                        f"inbox:{ctx.workspace_id}", "ticket.updated",
                        {"id": f"#{tid}", "status": "resolved"},
                    )
            elif outcome == "needs_human":
                await tool_escalate_to_human(ctx, reason=reason or "AI decided human follow-up needed")
        except GuardrailHit:
            raise
        except Exception:
            log.exception("post-reply outcome apply failed (outcome=%s)", outcome)
    return {"output": text, "outcome": outcome, "reason": reason, "steps": ctx.steps}


# ---------- public entry ----------------------------------------------------

async def run_default_orchestrator(ctx: AgentCtx, user_message: str,
                                   agent_id: str | None = None,
                                   model: str = "gpt-4.1-mini") -> dict:
    """Public entry point used by `POST /agents/:id/test` and the inbound AI
    reply pipeline. Records one `agent_runs` row + enforces guardrails before
    the tool surface ever runs."""
    started = time.perf_counter()
    status = "ok"
    output: dict[str, Any] = {}
    try:
        await _rate_limit_check(ctx)
        azure_key, azure_endpoint, _, _ = _azure_oai_creds()
        if os.environ.get("OPENAI_API_KEY"):
            try:
                output = await _pydantic_ai_run(ctx, user_message, model)
            except ImportError:
                log.info("pydantic-ai not installed — using heuristic fallback")
                output = await _fallback_run(ctx, user_message)
            except GuardrailHit as gh:
                status = "guardrail"
                output = {"output": None, "error": str(gh), "steps": ctx.steps}
                try:
                    await tool_escalate_to_human(ctx, reason=f"guardrail tripped: {gh}")
                except Exception:
                    pass
            except Exception as e:
                log.warning("pydantic-ai run failed (%s) — heuristic fallback", e)
                output = await _fallback_run(ctx, user_message)
        elif azure_key and azure_endpoint:
            try:
                output = await _azure_openai_run(ctx, user_message, model)
            except GuardrailHit as gh:
                status = "guardrail"
                output = {"output": None, "error": str(gh), "steps": ctx.steps}
            except Exception as e:
                log.warning("azure openai run failed (%s) — heuristic fallback", e)
                output = await _fallback_run(ctx, user_message)
        else:
            output = await _fallback_run(ctx, user_message)
    except GuardrailHit as gh:
        status = "guardrail"
        output = {"output": None, "error": str(gh), "steps": ctx.steps}
    except Exception as e:
        status = "error"
        output = {"output": None, "error": repr(e), "steps": ctx.steps}

    latency_ms = int((time.perf_counter() - started) * 1000)
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO agent_runs (workspace_id, agent_id, conversation_id, version,
                                    input, output, status, latency_ms, trace)
            VALUES (?,?,?, 0, ?, ?, ?, ?, ?)
            """,
            (ctx.workspace_id, agent_id, ctx.conversation_id,
             js({"message": user_message}),
             js(output), status, latency_ms,
             js({"traceId": ctx.trace_id, "steps": output.get("steps", ctx.steps),
                 "toolCalls": ctx.tool_calls})),
        )
        await db.commit()
    except Exception as e:
        log.warning("failed to record agent_run: %s", e)

    return {
        "traceId": ctx.trace_id,
        "status": status,
        "latencyMs": latency_ms,
        **output,
    }


# ---------- SSE streaming entry (spec §3.11.D, §10) --------------------------

async def stream_default_orchestrator(ctx: AgentCtx, user_message: str,
                                      agent_id: str | None = None,
                                      model: str = "gpt-4.1-mini"):
    """Async generator yielding SSE-shaped dicts for `POST /agents/:id/run`.
    Stage 1 emits a tool-event per step + final text — token-delta streaming
    lands when `pydantic-ai` exposes it cleanly."""
    yield {"event": "start", "data": {"traceId": ctx.trace_id}}
    result = await run_default_orchestrator(ctx, user_message, agent_id=agent_id, model=model)
    for step in result.get("steps", []):
        yield {"event": "tool", "data": step}
    if result.get("output"):
        yield {"event": "text", "data": {"text": result["output"]}}
    yield {"event": "done", "data": {"status": result.get("status"),
                                     "latencyMs": result.get("latencyMs")}}


# ---------- Inbound streaming into a live conversation (spec §10 auto-reply) -

async def stream_inbound_reply(ctx: AgentCtx, user_message: str,
                               model: str = "gpt-4.1-mini",
                               chunk_size: int = 24,
                               chunk_delay: float = 0.04) -> dict:
    """Run the orchestrator for an inbound customer message and fan the reply
    out on `conv:<id>` as a typing indicator + incremental `ai.delta` events
    followed by the saved `message.created` row. The SPA replaces the
    streaming bubble (keyed by `streamId`) with the persisted message when
    `streamFinal:True` arrives.

    Returns the orchestrator result so the caller can persist `agent_runs`
    and show latency. Requires `ctx.conversation_id` — bails otherwise."""
    if not ctx.conversation_id:
        return await run_default_orchestrator(ctx, user_message, agent_id=ctx.agent_id, model=model)

    from ..ws import broker
    topic = f"conv:{ctx.conversation_id}"
    stream_id = f"stream_{uuid.uuid4().hex[:8]}"
    agent_name = ctx.agent_name or "default-orchestrator"
    ctx.stream = True

    await broker.publish(topic, "ai.typing.started", {
        "streamId": stream_id, "agent": agent_name, "agentId": ctx.agent_id,
    })

    # Run the orchestrator to completion first. We don't yet have true token-
    # level deltas from pydantic-ai's streaming surface — instead we chunk the
    # final text in fixed-size slices so the UI still animates in real time.
    # When pydantic-ai exposes an `iter_text()` stream, swap this loop for it.
    result = await run_default_orchestrator(ctx, user_message,
                                            agent_id=ctx.agent_id, model=model)
    text = result.get("output") or ""
    emitted = 0
    if text:
        for i in range(0, len(text), max(1, chunk_size)):
            chunk = text[i: i + chunk_size]
            await broker.publish(topic, "ai.delta", {
                "streamId": stream_id,
                "delta": chunk,
                "index": emitted,
            })
            emitted += 1
            if chunk_delay > 0:
                await asyncio.sleep(chunk_delay)

    await broker.publish(topic, "ai.typing.stopped", {
        "streamId": stream_id, "agent": agent_name,
    })
    return result


# ---------- Agent selection for the inbound pipeline ------------------------

async def select_inbound_agent(workspace_id: str) -> dict:
    """Resolve the agent that should auto-reply on this workspace's inbound
    traffic. Order (spec §3.11 "Agent selection"):

      1. Any `is_active=true` Langflow-published agent — newest first.
      2. Any `status='published'` builtin agent.
      3. Fallback to the default-orchestrator placeholder row.
      4. If the workspace has `ai_force_default=true`, always use the default.

    Returns `{id, name, model, kind}` with the picked agent; `id` may be None
    when only the placeholder row exists."""
    db = await get_db()
    cur = await db.execute(
        "SELECT ai_force_default FROM workspaces WHERE id = ?", (workspace_id,)
    )
    row = await cur.fetchone()
    force_default = bool(row and row[0])

    if not force_default:
        cur = await db.execute(
            """
            SELECT id, name, model, kind
              FROM agents
             WHERE workspace_id = ? AND kind = 'langflow' AND is_active = true
             ORDER BY last_published_at DESC NULLS LAST, created_at DESC
             LIMIT 1
            """,
            (workspace_id,),
        )
        r = await cur.fetchone()
        if r:
            return dict(r)
        cur = await db.execute(
            """
            SELECT id, name, model, kind
              FROM agents
             WHERE workspace_id = ? AND status = 'published'
             ORDER BY last_published_at DESC NULLS LAST, created_at DESC
             LIMIT 1
            """,
            (workspace_id,),
        )
        r = await cur.fetchone()
        if r:
            return dict(r)

    cur = await db.execute(
        "SELECT id, name, model, kind FROM agents WHERE workspace_id = ? AND name = 'default-orchestrator' LIMIT 1",
        (workspace_id,),
    )
    r = await cur.fetchone()
    if r:
        return dict(r)
    return {"id": None, "name": "default-orchestrator", "model": "gpt-4.1-mini", "kind": "builtin"}
