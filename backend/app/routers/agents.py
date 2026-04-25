"""Agent Builder (spec §3.11).

Build 1: CRUD over `agents`, no Langflow/SSE. Adds a test runner stub that
returns a canned text reply so the frontend's 'Test agent' button shows output.
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

from ..db import get_db, js, jl
from ..deps import Me
from ..schemas import AgentIn

router = APIRouter(prefix="/agents", tags=["agents"])

# Second router mounted at root so the endpoint is `/ai/draft`, not
# `/agents/ai/draft` — composer shortcuts in the Conversation pane hit this.
draft_router = APIRouter(tags=["agents"])


@draft_router.post("/ai/draft")
async def ai_draft(body: dict, user: dict = Me) -> dict:
    """Ask the default orchestrator to draft a reply for a conversation,
    without persisting or sending. The composer inserts the returned text
    into the textarea so the agent can review before sending."""
    from ..ai.orchestrator import AgentCtx, run_default_orchestrator
    import uuid as _uuid

    conv_id = body.get("conversationId")
    if not conv_id:
        raise HTTPException(400, "conversationId required")
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND workspace_id = ?",
        (conv_id, user["workspace_id"]),
    )
    conv = await cur.fetchone()
    if not conv:
        raise HTTPException(404, "conversation not found")
    conv = dict(conv)

    # Build context from the conversation so the orchestrator can call
    # find_customer / list_customer_orders / similar_tickets against real data.
    # We intentionally DO NOT pass conversation_id so `send_reply` echoes the
    # text back instead of persisting / fanning out.
    ctx = AgentCtx(
        workspace_id=user["workspace_id"],
        conversation_id=None,
        customer_id=conv.get("customer_id"),
        channel=conv.get("channel"),
        actor_user_id=user["id"],
        trace_id=f"trc_{_uuid.uuid4().hex[:12]}",
    )

    # Use the last inbound message as the prompt; falls back to `preview`.
    cur = await db.execute(
        """
        SELECT text FROM messages
         WHERE conversation_id = ? AND from_role IN ('customer','them')
         ORDER BY at DESC LIMIT 1
        """,
        (conv_id,),
    )
    row = await cur.fetchone()
    prompt = (row[0] if row else None) or conv.get("preview") or "Draft a reply to this customer."

    out = await run_default_orchestrator(ctx, prompt, agent_id=None, model="gpt-4.1-mini")
    return {"draft": out.get("output") or "", "traceId": out.get("traceId"),
            "latencyMs": out.get("latencyMs")}


def _serialize(a: dict) -> dict:
    return {
        "id": a["id"],
        "name": a["name"],
        "model": a["model"],
        "persona": a["persona"],
        "instructions": a["instructions"],
        "temperature": a["temperature"],
        "tools": jl(a.get("tools"), []),
        "status": a.get("status", "draft"),
        "active_version": a.get("active_version", 1),
        "last_published_at": a.get("last_published_at"),
        # Surface `kind` + `langflow_flow_id` so the AgentBuilder can tell a
        # Langflow-backed flow from the default-orchestrator placeholder when
        # rendering the "Recent flows" picker, and `created_at` so the picker
        # orders newest-first without an extra round-trip.
        "kind": a.get("kind") or "default",
        "langflow_flow_id": a.get("langflow_flow_id"),
        "created_at": a.get("created_at"),
    }


@router.get("")
async def list_agents(user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM agents WHERE workspace_id = ?", (user["workspace_id"],))
    return [_serialize(dict(r)) for r in await cur.fetchall()]


@router.post("")
async def create_agent(body: AgentIn, user: dict = Me) -> dict:
    db = await get_db()
    aid = str(uuid4())
    await db.execute(
        """
        INSERT INTO agents (id, workspace_id, name, model, persona, instructions, temperature, tools, status)
        VALUES (?,?,?,?,?,?,?,?, 'draft')
        """,
        (aid, user["workspace_id"], body.name, body.model, body.persona,
         body.instructions, body.temperature, js(body.tools)),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM agents WHERE id = ?", (aid,))
    return _serialize(dict(await cur.fetchone()))


@router.get("/{aid}")
async def get_agent(aid: str, user: dict = Me) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM agents WHERE id = ? AND workspace_id = ?", (aid, user["workspace_id"])
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "agent not found")
    return _serialize(dict(row))


@router.patch("/{aid}")
async def patch_agent(aid: str, body: dict, user: dict = Me) -> dict:
    db = await get_db()
    allowed = {"name", "model", "persona", "instructions", "temperature", "status"}
    fields: dict = {}
    for k, v in body.items():
        if k in allowed:
            fields[k] = v
        elif k == "tools":
            fields["tools"] = js(v)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(
            f"UPDATE agents SET {sets} WHERE id = ? AND workspace_id = ?",
            (*fields.values(), aid, user["workspace_id"]),
        )
        await db.commit()
    return await get_agent(aid, user)


@router.delete("/{aid}")
async def delete_agent(aid: str, user: dict = Me) -> dict:
    db = await get_db()
    await db.execute(
        "DELETE FROM agents WHERE id = ? AND workspace_id = ?", (aid, user["workspace_id"])
    )
    await db.commit()
    return {"ok": True}


@router.post("/{aid}/test")
async def test_agent(aid: str, body: dict, user: dict = Me) -> dict:
    """Runs the default orchestrator in sandbox mode (no conversation, no
    customer). When `OPENAI_API_KEY` is set, uses pydantic-ai against OpenAI;
    otherwise falls back to the heuristic responder so the UI still works."""
    from ..ai.orchestrator import AgentCtx, run_default_orchestrator
    import uuid as _uuid

    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM agents WHERE id = ? AND workspace_id = ?", (aid, user["workspace_id"])
    )
    agent = await cur.fetchone()
    if not agent:
        raise HTTPException(404, "agent not found")
    agent = dict(agent)

    ctx = AgentCtx(
        workspace_id=user["workspace_id"],
        conversation_id=body.get("conversationId"),
        customer_id=body.get("customerId"),
        channel=body.get("channel"),
        actor_user_id=user["id"],
        trace_id=f"trc_{_uuid.uuid4().hex[:12]}",
    )
    out = await run_default_orchestrator(
        ctx,
        body.get("input") or body.get("message") or "",
        agent_id=aid,
        model=agent.get("model", "gpt-4.1-mini"),
    )
    return {"agent_id": aid, "at": datetime.now(timezone.utc).isoformat(), **out}


@router.post("/{aid}/run")
async def run_agent_sse(aid: str, body: dict, user: dict = Me) -> StreamingResponse:
    """SSE streaming endpoint for production runs (spec §3.11.D, §10). Each
    server-sent event is `event: <kind>\\ndata: <json>\\n\\n`. Kinds emitted:
    `start`, `tool`, `text`, `done`."""
    from ..ai.orchestrator import AgentCtx, stream_default_orchestrator
    import uuid as _uuid

    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM agents WHERE id = ? AND workspace_id = ?", (aid, user["workspace_id"])
    )
    agent = await cur.fetchone()
    if not agent:
        raise HTTPException(404, "agent not found")
    agent = dict(agent)

    ctx = AgentCtx(
        workspace_id=user["workspace_id"],
        conversation_id=body.get("conversationId"),
        customer_id=body.get("customerId"),
        channel=body.get("channel"),
        actor_user_id=user["id"],
        trace_id=f"trc_{_uuid.uuid4().hex[:12]}",
    )
    message = body.get("input") or body.get("message") or ""
    model = agent.get("model", "gpt-4.1-mini")

    async def generator():
        async for evt in stream_default_orchestrator(ctx, message, agent_id=aid, model=model):
            payload = json.dumps(evt.get("data", {}), default=str)
            yield f"event: {evt['event']}\ndata: {payload}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{aid}/chat")
async def chat_agent(aid: str, body: dict, user: dict = Me) -> dict:
    """Playground chat. For Langflow-backed agents we POST to
    `{LANGFLOW_URL}/api/v1/run/<flowId>` and unwrap the conventional
    `outputs[].outputs[].results.message.text` shape. Everything else falls
    back to the default orchestrator so the AgentBuilder chat pane works even
    before a flow is exported."""
    import uuid as _uuid
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM agents WHERE id = ? AND workspace_id = ?", (aid, user["workspace_id"])
    )
    agent = await cur.fetchone()
    if not agent:
        raise HTTPException(404, "agent not found")
    agent = dict(agent)

    message = (body.get("input") or body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "input is required")
    session_id = body.get("sessionId") or f"wx-playground-{aid[:8]}"

    flow_id = agent.get("langflow_flow_id")
    kind = (agent.get("kind") or "default").lower()

    if kind == "langflow" and flow_id:
        from .channels import _invoke_langflow_flow
        # Authenticate as the calling user, not the superuser. Langflow's
        # `/api/v1/run/<flowId>` enforces that only the flow's owner can
        # execute it on most builds, so the superuser bearer we'd otherwise
        # mint here comes back as 403. The user's Langflow account was
        # provisioned at first `/flow/launch` and their password is stashed
        # encrypted in `users.langflow_password_secret_id`.
        from .langflow import (
            _mint_access_token, _provision_user_and_folder, ensure_flow_alive,
        )
        # Before we try to run, make sure Langflow still has this flow.
        # Langflow's container DB is ephemeral in most deploys; if it was
        # restarted since the last save, the stored flow id dangles. We
        # rehydrate from the MinIO snapshot written by /flow/save-as-agent.
        live_flow_id = await ensure_flow_alive(
            db, agent_id=aid, workspace_id=user["workspace_id"],
        ) or flow_id
        user_bearer = await _mint_access_token(db, user, None)
        if not user_bearer:
            # No stashed password yet (user never launched the editor) —
            # provision on the fly so the chat still works.
            try:
                await _provision_user_and_folder(db, user)
                user_bearer = await _mint_access_token(db, user, None)
            except Exception:
                log.exception("chat_agent: user bearer mint failed")
        spec = {"kind": "langflow", "flowId": live_flow_id}
        args = {"input": message, "tweaks": {"session_id": session_id}}
        status, payload = await _invoke_langflow_flow(spec, args, bearer=user_bearer)
        if status >= 400:
            err = (payload or {}).get("error") or (payload or {}).get("text") or f"langflow {status}"
            raise HTTPException(status if status < 600 else 502, str(err)[:500])
        reply = _extract_langflow_reply(payload) or "(no reply)"
        return {
            "agentId": aid,
            "sessionId": session_id,
            "reply": reply,
            "at": datetime.now(timezone.utc).isoformat(),
            "source": "langflow",
        }

    from ..ai.orchestrator import AgentCtx, run_default_orchestrator
    ctx = AgentCtx(
        workspace_id=user["workspace_id"],
        conversation_id=None,
        customer_id=None,
        channel=None,
        actor_user_id=user["id"],
        trace_id=f"trc_{_uuid.uuid4().hex[:12]}",
    )
    out = await run_default_orchestrator(
        ctx, message, agent_id=aid, model=agent.get("model", "gpt-4.1-mini")
    )
    return {
        "agentId": aid,
        "sessionId": session_id,
        "reply": out.get("output") or "(no reply)",
        "at": datetime.now(timezone.utc).isoformat(),
        "source": "orchestrator",
        "traceId": out.get("traceId"),
    }


def _extract_langflow_reply(payload: dict) -> str | None:
    """Walk Langflow's `/api/v1/run` envelope for the textual reply. Shape:
    `{outputs: [{outputs: [{results: {message: {text}}, artifacts: {message},
    outputs: {message: {message}}}]}]}`. Different component types stash the
    text in different slots, so we probe each one and bail at the first hit."""
    if not isinstance(payload, dict):
        return None
    outer = payload.get("outputs") or []
    if not isinstance(outer, list):
        return None
    for o in outer:
        inner = (o or {}).get("outputs") or []
        if not isinstance(inner, list):
            continue
        for item in inner:
            if not isinstance(item, dict):
                continue
            results = item.get("results") or {}
            msg = results.get("message") if isinstance(results, dict) else None
            if isinstance(msg, dict) and msg.get("text"):
                return str(msg["text"])
            arts = item.get("artifacts") or {}
            if isinstance(arts, dict) and isinstance(arts.get("message"), str):
                return arts["message"]
            outs = item.get("outputs") or {}
            if isinstance(outs, dict):
                m = outs.get("message") or {}
                if isinstance(m, dict):
                    inner_msg = m.get("message")
                    if isinstance(inner_msg, dict) and inner_msg.get("text"):
                        return str(inner_msg["text"])
                    if isinstance(inner_msg, str):
                        return inner_msg
            if isinstance(item.get("text"), str):
                return item["text"]
    return None


@router.get("/{aid}/runs")
async def list_agent_runs(aid: str, limit: int = 50, user: dict = Me) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, version, status, latency_ms, tokens_in, tokens_out, cost_usd, at
          FROM agent_runs
         WHERE agent_id = ? AND workspace_id = ?
         ORDER BY at DESC LIMIT ?
        """,
        (aid, user["workspace_id"], max(1, min(limit, 200))),
    )
    return [
        {
            "id": r["id"],
            "version": r["version"],
            "status": r["status"],
            "latencyMs": r["latency_ms"],
            "tokensIn": r.get("tokens_in"),
            "tokensOut": r.get("tokens_out"),
            "costUsd": r.get("cost_usd"),
            "at": r["at"],
        }
        for r in [dict(x) for x in await cur.fetchall()]
    ]
