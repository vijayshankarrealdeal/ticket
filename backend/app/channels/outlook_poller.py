"""Outlook / Microsoft 365 polling adapter.

Mirrors `gmail_poller.py`. Graph push subscriptions need a public HTTPS URL —
not an option on localhost — so we poll the mailbox via
`/me/mailFolders/inbox/messages?$filter=isRead eq false` every 30 s and
funnel each unread message through the shared `ingest_inbound_message`
pipeline.

Refresh-token handling: on 401 we exchange the stored refresh_token at
`login.microsoftonline.com/.../token` and persist the new access token so
the poller self-heals across the 1-hour access-token TTL.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from ..db import get_db, jl, js

log = logging.getLogger(__name__)

GRAPH_API = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def _outlook_client_creds() -> tuple[str | None, str | None]:
    cid = (
        os.environ.get("OUTLOOK_CLIENT_ID")
        or os.environ.get("MICROSOFT_CLIENT_ID")
    )
    sec = (
        os.environ.get("OUTLOOK_CLIENT_SECRET")
        or os.environ.get("MICROSOFT_CLIENT_SECRET")
    )
    return cid, sec


async def _refresh_access_token(db, cred_id: str, refresh_token: str) -> str | None:
    cid, sec = _outlook_client_creds()
    if not cid or not sec:
        log.warning("outlook refresh skipped — microsoft client creds missing")
        return None
    try:
        import httpx  # type: ignore
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": cid, "client_secret": sec,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "openid email offline_access Mail.Read Mail.Send Mail.ReadWrite",
                },
            )
    except Exception as e:
        log.warning("outlook refresh http error: %r", e)
        return None
    if resp.status_code >= 400:
        log.warning("outlook refresh failed (%s): %s", resp.status_code, resp.text[:200])
        if resp.status_code in (400, 401):
            await db.execute(
                "UPDATE platform_credentials SET status = 'reauth_required' WHERE id = ?",
                (cred_id,),
            )
            await db.commit()
        return None
    data = resp.json()
    new_access = data.get("access_token")
    if not new_access:
        return None
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in") or 3600))
    ).isoformat()
    cur = await db.execute(
        "SELECT secrets FROM platform_credentials WHERE id = ?", (cred_id,)
    )
    row = dict(await cur.fetchone() or {})
    secrets = jl(row.get("secrets"), {}) or {}
    secrets["access_token"] = new_access
    if data.get("refresh_token"):
        secrets["refresh_token"] = data["refresh_token"]
    await db.execute(
        """
        UPDATE platform_credentials
           SET secrets = ?, status = 'active', expires_at = ?
         WHERE id = ?
        """,
        (js(secrets), expires, cred_id),
    )
    await db.commit()
    return new_access


async def _graph_get(url: str, token: str, params: dict | None = None) -> tuple[int, dict | str]:
    try:
        import httpx  # type: ignore
    except ImportError:
        return 0, "httpx not installed"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url, params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        return 0, repr(e)
    try:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    except Exception:
        body = resp.text
    return resp.status_code, body


async def _mark_read(token: str, message_id: str) -> None:
    try:
        import httpx  # type: ignore
    except ImportError:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"{GRAPH_API}/me/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={"isRead": True},
            )
    except Exception:
        pass


def _strip_html(html: str) -> str:
    import html as _html
    import re
    for block in ("style", "script", "head", "title", "noscript"):
        html = re.sub(rf"<{block}\b[^>]*>.*?</{block}>", " ",
                      html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", html)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


async def poll_outlook_for_credential(db, cred_row: dict) -> dict:
    from ..routers.conversations import (
        ingest_inbound_message,
        resolve_or_create_conversation,
        resolve_or_create_customer,
    )

    secrets = jl(cred_row.get("secrets"), {}) or {}
    token = secrets.get("access_token")
    refresh = secrets.get("refresh_token")
    ws_id = cred_row["workspace_id"]
    cred_id = cred_row["id"]
    if not token or token.startswith("dev_"):
        return {"ingested": 0, "fetched": 0, "note": "dev-token"}

    select_fields = (
        "id,internetMessageId,subject,bodyPreview,from,sender,"
        "receivedDateTime,conversationId,body,isRead"
    )
    list_url = f"{GRAPH_API}/me/mailFolders/inbox/messages"
    list_params = {
        "$filter": "isRead eq false",
        "$top": "20",
        "$select": select_fields,
        "$orderby": "receivedDateTime desc",
    }
    status, body = await _graph_get(list_url, token, list_params)
    if status == 401 and refresh:
        new_token = await _refresh_access_token(db, cred_id, refresh)
        if not new_token:
            return {"ingested": 0, "fetched": 0, "error": "token refresh failed"}
        token = new_token
        status, body = await _graph_get(list_url, token, list_params)
    if status != 200 or not isinstance(body, dict):
        return {"ingested": 0, "fetched": 0,
                "error": f"list {status}: {str(body)[:200]}"}

    messages = body.get("value") or []
    ingested = 0

    for m in messages:
        mid = m.get("id")
        if not mid:
            continue
        rfc_msgid = (m.get("internetMessageId") or "").strip() or mid
        # Dedupe by RFC-822 Message-ID — reusing the same key Gmail uses keeps
        # the threading helpers in conversations.py / orchestrator.py uniform.
        cur = await db.execute(
            "SELECT 1 FROM messages WHERE workspace_id = ? AND external_message_id = ? LIMIT 1",
            (ws_id, rfc_msgid),
        )
        if await cur.fetchone():
            await _mark_read(token, mid)
            continue

        from_obj = (m.get("from") or m.get("sender") or {}).get("emailAddress") or {}
        from_email = (from_obj.get("address") or "").lower()
        from_name = from_obj.get("name") or (from_email.split("@", 1)[0] if from_email else "Outlook")
        # Skip outbound/no-reply senders for the same reason gmail_poller does.
        local = from_email.split("@", 1)[0]
        if any(tag in local for tag in (
            "no-reply", "noreply", "donotreply", "do-not-reply",
            "mailer-daemon", "postmaster",
        )):
            await _mark_read(token, mid)
            continue

        subject = m.get("subject") or ""
        thread_id = m.get("conversationId") or mid
        body_obj = m.get("body") or {}
        if (body_obj.get("contentType") or "").lower() == "html":
            text_body = _strip_html(body_obj.get("content") or "")
        else:
            text_body = (body_obj.get("content") or m.get("bodyPreview") or "").strip()

        try:
            customer_id = await resolve_or_create_customer(
                workspace_id=ws_id, email=from_email or None, name=from_name,
            )
            conv_id = await resolve_or_create_conversation(
                workspace_id=ws_id, customer_id=customer_id, channel="outlook",
                external_thread_id=thread_id,
            )
            if subject:
                import re
                base_subject = re.sub(r"^\s*(re|fwd|fw)\s*:\s*", "",
                                      subject, flags=re.IGNORECASE).strip()
                await db.execute(
                    """
                    UPDATE conversations
                       SET subject = ?
                     WHERE id = ? AND (subject IS NULL OR subject = '')
                    """,
                    (base_subject or subject, conv_id),
                )
                await db.commit()
            await ingest_inbound_message(
                workspace_id=ws_id, conversation_id=conv_id,
                text=text_body.strip(),
                from_name=from_name,
                external_message_id=rfc_msgid,
            )
            ingested += 1
        except Exception:
            log.exception("outlook ingest failed (mid=%s)", mid)
            continue
        await _mark_read(token, mid)

    return {"ingested": ingested, "fetched": len(messages)}


async def poll_all_outlook_accounts(workspace_id: str | None = None) -> dict:
    db = await get_db()
    clauses = ["provider = 'outlook'", "status = 'active'"]
    params: list = []
    if workspace_id:
        clauses.append("workspace_id = ?"); params.append(workspace_id)
    sql = f"SELECT * FROM platform_credentials WHERE {' AND '.join(clauses)} ORDER BY created_at"
    cur = await db.execute(sql, tuple(params))
    rows = [dict(r) for r in await cur.fetchall()]
    totals = {"accounts": 0, "ingested": 0, "fetched": 0, "errors": []}
    for r in rows:
        result = await poll_outlook_for_credential(db, r)
        totals["accounts"] += 1
        totals["ingested"] += int(result.get("ingested") or 0)
        totals["fetched"] += int(result.get("fetched") or 0)
        if result.get("error"):
            totals["errors"].append({"account": r.get("account_id"),
                                     "error": result["error"]})
    return totals
