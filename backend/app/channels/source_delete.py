"""Source-side conversation deletion helpers.

When an agent deletes a conversation from the inbox we also try to remove the
underlying thread at the source (Gmail trash, Outlook delete) so it doesn't
reappear on the next poll and so the customer-facing mailbox mirrors the
agent's intent. Best-effort: failures here must not block the local cascade —
the caller still deletes the conversation/tickets so stale rows don't linger
when a token has been revoked.
"""
from __future__ import annotations

import logging

from ..db import get_db, jl

log = logging.getLogger(__name__)


async def _delete_outlook_messages(workspace_id: str, external_message_ids: list[str]) -> tuple[bool, str | None]:
    """Graph `DELETE /me/messages/{id}` — moves each message to Deleted Items.
    Outlook has no per-thread trash endpoint, so we iterate the messages we
    ingested and delete them one by one."""
    ids = [m for m in (external_message_ids or []) if m]
    if not ids:
        return False, "no outlook message ids to delete"
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, secrets FROM platform_credentials
         WHERE workspace_id = ? AND provider = 'outlook' AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (workspace_id,),
    )
    row = await cur.fetchone()
    if not row:
        return False, "no active outlook credential"
    secrets = jl(dict(row).get("secrets"), {}) or {}
    token = secrets.get("access_token")
    if not token or token.startswith("dev_"):
        return False, "dev-token or missing access_token"
    try:
        import httpx  # type: ignore
    except ImportError:
        return False, "httpx not installed"
    failed: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for mid in ids:
                resp = await client.delete(
                    f"https://graph.microsoft.com/v1.0/me/messages/{mid}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                # 204 = deleted, 404 = already gone (treat as success).
                if resp.status_code not in (204, 404):
                    failed.append(f"{mid}:{resp.status_code}")
    except Exception as e:
        return False, repr(e)[:300]
    if failed:
        return False, f"graph delete errors: {','.join(failed[:3])}"
    return True, None


async def delete_conversation_at_source(
    *, workspace_id: str, channel: str | None,
    external_thread_id: str | None,
    external_message_ids: list[str],
) -> dict:
    """Dispatch to the right per-channel trash/delete call. Returns a summary
    `{channel, ok, error?}` the caller can surface in logs / API response.
    Channels with no deletion primitive (WhatsApp Cloud API doesn't support
    deleting for the recipient) return `ok=False` with a note."""
    ch = (channel or "").lower()
    if ch in ("gmail", "google"):
        from .gmail_poller import trash_gmail_thread
        if not external_thread_id:
            return {"channel": ch, "ok": False, "error": "no external_thread_id"}
        ok, err = await trash_gmail_thread(workspace_id, external_thread_id)
        return {"channel": ch, "ok": ok, "error": err}
    if ch == "outlook":
        ok, err = await _delete_outlook_messages(workspace_id, external_message_ids)
        return {"channel": ch, "ok": ok, "error": err}
    # WhatsApp Cloud API, Instagram DM, Live Chat, Voice, Slack: no
    # server-side delete-for-recipient primitive we can rely on.
    return {"channel": ch, "ok": False, "error": "no source-delete for channel"}
