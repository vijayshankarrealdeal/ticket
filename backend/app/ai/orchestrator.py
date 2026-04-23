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
    """Build-2 knowledge base search. Until pgvector lands we fall back to
    trigram/ILIKE over `kb_chunks.body` — same interface as the planned HNSW
    call so we can swap it in without touching the tool."""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, title, body, source
          FROM kb_chunks
         WHERE workspace_id = ?
           AND (body ILIKE ? OR title ILIKE ?)
         LIMIT ?
        """,
        (ctx.workspace_id, f"%{query}%", f"%{query}%", max(1, min(k, 10))),
    )
    return [dict(r) for r in await cur.fetchall()]


@_guard_tool("similar_tickets")
async def tool_similar_tickets(ctx: AgentCtx, subject: str) -> list[dict]:
    db = await get_db()
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
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'queued', ?)
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
                         {"id": tid, "subject": subject, "task": task})
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
    await broker.publish(f"ticket:{ticket_id}", "ticket.updated", {"id": ticket_id, **updates})
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
    real adapter. Without a conversation context (test mode) we just echo."""
    if not ctx.conversation_id:
        return {"ok": True, "echoed": text}
    db = await get_db()
    mid = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO messages (id, workspace_id, conversation_id, from_role, from_name, text, attachments)
        VALUES (?, ?, ?, 'ai', 'default-orchestrator', ?, '[]'::jsonb)
        """,
        (mid, ctx.workspace_id, ctx.conversation_id, text),
    )
    await db.execute(
        "UPDATE conversations SET last_message_at = now(), preview = ? WHERE id = ?",
        (text[:160], ctx.conversation_id),
    )
    await db.execute(
        "INSERT INTO outbox (workspace_id, kind, payload) VALUES (?, ?, ?)",
        (ctx.workspace_id, f"send_{(ctx.channel or 'generic').lower()}",
         js({"conversationId": ctx.conversation_id, "text": text, "messageId": mid})),
    )
    await db.commit()
    # Fan out so the conversation pane animates the bubble.
    from ..ws import broker
    await broker.publish(
        f"conv:{ctx.conversation_id}", "message.created",
        {"id": mid, "from": "me", "text": text, "at": None,
         "fromName": "default-orchestrator", "delivered": True},
    )
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
    # Find any admin/agent in the workspace as the recipient.
    cur = await db.execute(
        "SELECT id FROM users WHERE workspace_id = ? AND role IN ('admin','agent') LIMIT 1",
        (ctx.workspace_id,),
    )
    r = await cur.fetchone()
    if r:
        await create_notification(
            r[0], "assign",
            title="AI escalated a conversation",
            body=reason,
            link=f"/conversations?id={ctx.conversation_id}" if ctx.conversation_id else "/conversations",
        )
    # Best-effort: update linked ticket status to 'blocked' so a human picks it up.
    if ctx.conversation_id:
        await db.execute(
            """
            UPDATE tickets SET status = 'blocked', updated_at = now()
             WHERE conversation_id = ? AND status NOT IN ('resolved','closed')
            """,
            (ctx.conversation_id,),
        )
        await db.commit()
    return {"ok": True, "team": team, "reason": reason}


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

    agent = Agent(
        model=f"openai:{model}",
        deps_type=AgentCtx,
        retries=2,
        system_prompt=(
            "You are WorkspaceX's AI support agent. Always call tools rather than inventing data. "
            "Prefer `send_reply` for customer-facing text; `escalate_to_human` when unsure. "
            f"Never issue a refund above ₹{REFUND_CAP_INR}. Never call more than "
            f"{LOOP_GUARD_TOOL_CALLS} tools in a single turn."
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
    """Async generator yielding SSE-shaped dicts for the conversation pane.
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
