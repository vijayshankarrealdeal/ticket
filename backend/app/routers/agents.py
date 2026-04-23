"""Agent Builder (spec §3.11).

Build 1: CRUD over `agents`, no Langflow/SSE. Adds a test runner stub that
returns a canned text reply so the frontend's 'Test agent' button shows output.
"""
from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..db import get_db, js, jl
from ..deps import Me
from ..schemas import AgentIn

router = APIRouter(prefix="/agents", tags=["agents"])


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
    # Stub — real path runs `default-orchestrator` via pydantic-ai (spec §3.11.H).
    prompt = body.get("input", "")
    return {
        "agent_id": aid,
        "output": f"[mock reply for {prompt!r}] I'd normally check your orders and reply here. "
                  f"Wire OPENAI_API_KEY + pydantic-ai to make this real — see BACKEND_SPEC.md §3.11.H.",
        "at": datetime.now(timezone.utc).isoformat(),
    }
