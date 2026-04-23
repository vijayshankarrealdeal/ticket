"""Gmail polling adapter.

Pub/Sub push (spec §6.1 "recommended") needs a public HTTPS URL Google can
reach, which you don't have on localhost. This module is the practical
alternative: poll the Gmail API using the OAuth tokens stored in
`platform_credentials(provider='gmail')`, pull new messages, and funnel each
one through the shared `ingest_inbound_message` helper so it lands in
Conversations exactly like a webhook-driven inbound would.

Used by:
- `_gmail_poller_tick` in [worker.py](../worker.py) — runs every 30 s.
- `POST /platforms/gmail/sync` in [routers/platforms.py](../routers/platforms.py)
  so the operator can force an immediate pull from the UI.

Refresh-token handling: if Gmail returns 401 we exchange the stored refresh
token at `oauth2.googleapis.com/token` and persist the new access token +
expiry, so the poller self-heals across Google's 1 h access-token TTL.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from ..db import get_db, js, jl

log = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
REFRESH_URL = "https://oauth2.googleapis.com/token"


def _google_client_creds() -> tuple[str | None, str | None]:
    """Accept `GOOGLE_CLIENT_{ID,SECRET}` or the generic `Client_{ID,Secret}`
    naming some .env files use — matches routers/oauth.py precedence."""
    cid = (os.environ.get("GOOGLE_CLIENT_ID")
           or os.environ.get("Client_ID")
           or os.environ.get("CLIENT_ID"))
    sec = (os.environ.get("GOOGLE_CLIENT_SECRET")
           or os.environ.get("Client_Secret")
           or os.environ.get("CLIENT_SECRET"))
    return cid, sec


async def _refresh_access_token(db, cred_id: str, refresh_token: str) -> str | None:
    """Exchange a refresh token for a new access token. Writes the new
    access_token + expiry back onto the credential row and returns it. On
    failure (expired refresh token etc.) flips `status='reauth_required'` so
    the UI can nudge the admin to reconnect."""
    cid, sec = _google_client_creds()
    if not cid or not sec:
        log.warning("gmail refresh skipped — google client creds missing")
        return None
    try:
        import httpx  # type: ignore
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                REFRESH_URL,
                data={"client_id": cid, "client_secret": sec,
                      "refresh_token": refresh_token,
                      "grant_type": "refresh_token"},
            )
    except Exception as e:
        log.warning("gmail refresh http error: %r", e)
        return None
    if resp.status_code >= 400:
        log.warning("gmail refresh failed (%s): %s", resp.status_code, resp.text[:200])
        # Only flag reauth when the refresh token itself is bad.
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
    expires = (datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in") or 3600))).isoformat()
    # Merge into existing secrets blob so we don't clobber refresh_token.
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
        UPDATE platform_credentials SET secrets = ?, status = 'active',
               expires_at = ? WHERE id = ?
        """,
        (js(secrets), expires, cred_id),
    )
    await db.commit()
    return new_access


async def _gmail_get(url: str, token: str, params: dict | None = None) -> tuple[int, dict | str]:
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


def _decode_b64url(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * ((4 - len(data) % 4) % 4))
    except Exception:
        return b""


def _strip_html(html: str) -> str:
    """Turn a marketing-email HTML blob into something the inbox view can
    render without looking like a stylesheet dump. Removes <style>/<script>/
    <head> blocks wholesale, strips the remaining tags, decodes the common
    entities, and collapses runs of whitespace."""
    import html as _html
    import re
    # Drop entire <style>, <script>, <head> blocks (their bodies are CSS/JS
    # which would otherwise survive the tag-strip pass).
    for block in ("style", "script", "head", "title", "noscript"):
        html = re.sub(rf"<{block}\b[^>]*>.*?</{block}>", " ",
                      html, flags=re.IGNORECASE | re.DOTALL)
    # Drop HTML comments (conditional comments / tracker pixels).
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    # Tag strip + entity decode.
    text = re.sub(r"<[^>]+>", " ", html)
    text = _html.unescape(text)
    # Collapse whitespace (newlines, tabs, multiple spaces → single space).
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_text(payload: dict) -> str:
    """Walk the MIME tree and pick the first `text/plain` body. If none
    exists, fall back to a cleaned-up version of the HTML body. Gmail nests
    alternative parts several levels deep, so recurse."""
    if not payload:
        return ""
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}
    data = body.get("data")
    if mime.startswith("text/plain") and data:
        raw = _decode_b64url(data).decode(errors="replace")
        import re
        # Trim aggressive whitespace but keep paragraph breaks.
        lines = [l.strip() for l in raw.splitlines()]
        return "\n".join(l for l in lines if l).strip()
    if mime.startswith("text/html") and data:
        return _strip_html(_decode_b64url(data).decode(errors="replace"))
    for part in payload.get("parts") or []:
        out = _extract_text(part)
        if out:
            return out
    return ""


def _headers_map(headers: list[dict]) -> dict[str, str]:
    return {(h.get("name") or "").lower(): h.get("value") or "" for h in headers or []}


async def poll_gmail_for_credential(db, cred_row: dict) -> dict:
    """Pull unread inbound messages for one credential row. Returns
    `{ingested, fetched, error?}` so the caller can surface progress."""
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
        # Dev-mode creds can't call real Gmail — skip quietly.
        return {"ingested": 0, "fetched": 0, "note": "dev-token"}

    # 1) List unread inbox messages, skipping:
    #    - our own outbound (would bounce the AI's replies back in)
    #    - automated senders (no-reply, notifications, security alerts) — the
    #      AI would cheerfully reply to newsletters otherwise
    #    - category:promotions / social to keep demos clean
    gmail_query = (
        "is:unread -in:sent -in:chats "
        "-from:noreply -from:no-reply -from:donotreply -from:do-not-reply "
        "-from:notifications -from:mailer-daemon -from:postmaster "
        "-category:promotions -category:social"
    )
    status, body = await _gmail_get(
        f"{GMAIL_API}/messages", token,
        {"q": gmail_query, "maxResults": 20, "labelIds": "INBOX"},
    )
    if status == 401 and refresh:
        new_token = await _refresh_access_token(db, cred_id, refresh)
        if not new_token:
            return {"ingested": 0, "fetched": 0, "error": "token refresh failed"}
        token = new_token
        status, body = await _gmail_get(
            f"{GMAIL_API}/messages", token,
            {"q": gmail_query, "maxResults": 20, "labelIds": "INBOX"},
        )
    if status != 200 or not isinstance(body, dict):
        return {"ingested": 0, "fetched": 0, "error": f"list {status}: {str(body)[:200]}"}

    msg_ids = [m.get("id") for m in (body.get("messages") or []) if m.get("id")]
    ingested = 0

    for mid in msg_ids:
        # Fetch the full message first so we can dedupe by the RFC-822
        # Message-ID — that's the stable identifier we'll need later for
        # In-Reply-To/References threading, so we use it as the row's
        # external_message_id too. Falls back to the Gmail internal id if
        # the header is missing.
        status, full = await _gmail_get(
            f"{GMAIL_API}/messages/{mid}", token, {"format": "full"},
        )
        if status != 200 or not isinstance(full, dict):
            log.warning("gmail fetch %s failed: %s", mid, status)
            continue
        payload = full.get("payload") or {}
        headers = _headers_map(payload.get("headers") or [])
        rfc_msgid = (headers.get("message-id") or "").strip() or mid
        cur = await db.execute(
            "SELECT 1 FROM messages WHERE workspace_id = ? AND external_message_id = ? LIMIT 1",
            (ws_id, rfc_msgid),
        )
        if await cur.fetchone():
            await _mark_read(token, mid)
            continue

        from_header = headers.get("from") or ""
        from_name, from_email = parseaddr(from_header)
        from_name = from_name or (from_email.split("@", 1)[0] if from_email else "Gmail")
        subject = headers.get("subject") or ""
        thread_id = full.get("threadId") or mid
        text_body = _extract_text(payload) or full.get("snippet") or ""

        try:
            customer_id = await resolve_or_create_customer(
                workspace_id=ws_id, email=from_email or None, name=from_name,
            )
            conv_id = await resolve_or_create_conversation(
                workspace_id=ws_id, customer_id=customer_id, channel="gmail",
                external_thread_id=thread_id,
            )
            # Persist the email Subject on the conversation so the AI's
            # outbound reply can reuse it verbatim — Gmail requires matching
            # subjects for threadId-based threading. Strip any leading
            # `Re:` / `Fwd:` prefixes so replies don't stack into
            # `Re: Re: Re: foo`; the orchestrator adds a single `Re:` on
            # send. Only overwrite when the stored value is NULL or empty.
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
            # Persist the inbound. `external_message_id` = RFC-822 Message-ID
            # so the AI orchestrator can set `In-Reply-To`/`References` on the
            # outbound reply (what threads the conversation in the recipient's
            # mail client — Gmail's threadId is local to our own mailbox).
            await ingest_inbound_message(
                workspace_id=ws_id, conversation_id=conv_id,
                text=text_body.strip(),
                from_name=from_name,
                external_message_id=rfc_msgid,
            )
            ingested += 1
        except Exception:
            log.exception("gmail ingest failed (mid=%s)", mid)
            continue

        # Remove the UNREAD label so this message won't come back next tick.
        await _mark_read(token, mid)

    return {"ingested": ingested, "fetched": len(msg_ids)}


async def trash_gmail_thread(workspace_id: str, thread_id: str) -> tuple[bool, str | None]:
    """Move a Gmail thread to Trash via `users.threads.trash`. Looks up the
    workspace's active Gmail credential, auto-refreshes the access token on
    401, and returns `(ok, error)`. Best-effort — the caller should still
    delete the local rows even when the source call fails (disconnected
    account, revoked token, etc.)."""
    if not thread_id:
        return False, "missing thread_id"
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, secrets FROM platform_credentials
         WHERE workspace_id = ? AND provider = 'gmail' AND status = 'active'
         ORDER BY created_at DESC LIMIT 1
        """,
        (workspace_id,),
    )
    row = await cur.fetchone()
    if not row:
        return False, "no active gmail credential"
    cred = dict(row)
    secrets = jl(cred.get("secrets"), {}) or {}
    token = secrets.get("access_token")
    refresh = secrets.get("refresh_token")
    if not token or token.startswith("dev_"):
        return False, "dev-token or missing access_token"
    try:
        import httpx  # type: ignore
    except ImportError:
        return False, "httpx not installed"

    async def _do_trash(access_token: str) -> tuple[int, str]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GMAIL_API}/threads/{thread_id}/trash",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        return resp.status_code, resp.text

    try:
        status, txt = await _do_trash(token)
        if status == 401 and refresh:
            new_token = await _refresh_access_token(db, cred["id"], refresh)
            if new_token:
                status, txt = await _do_trash(new_token)
        if status >= 400:
            return False, f"HTTP {status}: {txt[:200]}"
        return True, None
    except Exception as e:
        return False, repr(e)[:300]


async def _mark_read(token: str, mid: str) -> None:
    try:
        import httpx  # type: ignore
    except ImportError:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{GMAIL_API}/messages/{mid}/modify",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                content=json.dumps({"removeLabelIds": ["UNREAD"]}),
            )
    except Exception:
        pass


async def poll_all_gmail_accounts(workspace_id: str | None = None) -> dict:
    """Fan out the per-credential poller across every active Gmail account.
    Scoped to `workspace_id` when provided so the manual-sync endpoint
    doesn't leak work to other tenants."""
    db = await get_db()
    clauses = ["provider = 'gmail'", "status = 'active'"]
    params: list = []
    if workspace_id:
        clauses.append("workspace_id = ?"); params.append(workspace_id)
    sql = f"SELECT * FROM platform_credentials WHERE {' AND '.join(clauses)} ORDER BY created_at"
    cur = await db.execute(sql, tuple(params))
    rows = [dict(r) for r in await cur.fetchall()]
    totals = {"accounts": 0, "ingested": 0, "fetched": 0, "errors": []}
    for r in rows:
        result = await poll_gmail_for_credential(db, r)
        totals["accounts"] += 1
        totals["ingested"] += int(result.get("ingested") or 0)
        totals["fetched"] += int(result.get("fetched") or 0)
        if result.get("error"):
            totals["errors"].append({"account": r.get("account_id"),
                                     "error": result["error"]})
    return totals
