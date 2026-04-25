"""IMAP mailbox adapter (spec §6).

Polls an IMAP INBOX every 60 s and runs each unread message through the
shared `ingest_inbound_message` pipeline so inbound mail from any Gmail /
Outlook / custom mailbox flows into Conversations without depending on
provider-specific OAuth.

Outbound: `_send_imap_smtp` in worker.py sends via SMTP with the same stored
credentials. App-password flows on Gmail/Outlook rely on this adapter.
"""
from __future__ import annotations

import logging
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr

from ..db import get_db, jl, js

log = logging.getLogger(__name__)


def _decode(header_val: str | None) -> str:
    if not header_val:
        return ""
    out: list[str] = []
    for chunk, enc in decode_header(header_val):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except Exception:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk or "")
    return "".join(out).strip()


def _extract_body(msg) -> str:
    """Walk the MIME tree, prefer text/plain, fall back to stripped HTML."""
    import re
    import html as _html

    def _strip_html(blob: str) -> str:
        for tag in ("style", "script", "head", "title", "noscript"):
            blob = re.sub(
                rf"<{tag}\b[^>]*>.*?</{tag}>", " ", blob,
                flags=re.IGNORECASE | re.DOTALL,
            )
        blob = re.sub(r"<!--.*?-->", " ", blob, flags=re.DOTALL)
        blob = re.sub(r"<[^>]+>", " ", blob)
        blob = _html.unescape(blob)
        return re.sub(r"\s+", " ", blob).strip()

    plain: str | None = None
    html_body: str | None = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            if ctype == "text/plain" and plain is None:
                payload = part.get_payload(decode=True) or b""
                plain = payload.decode(part.get_content_charset() or "utf-8",
                                       errors="replace")
            elif ctype == "text/html" and html_body is None:
                payload = part.get_payload(decode=True) or b""
                html_body = payload.decode(part.get_content_charset() or "utf-8",
                                           errors="replace")
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html_body = text
        else:
            plain = text
    if plain:
        lines = [l.strip() for l in plain.splitlines()]
        return "\n".join(l for l in lines if l).strip()
    if html_body:
        return _strip_html(html_body)
    return ""


async def poll_imap_for_credential(db, cred_row: dict) -> dict:
    """Blocking imaplib is wrapped in `asyncio.to_thread` so the polling loop
    doesn't stall the rest of the worker. Marks ingested messages as Seen so
    the next tick skips them."""
    import asyncio

    from ..routers.conversations import (
        ingest_inbound_message,
        resolve_or_create_conversation,
        resolve_or_create_customer,
    )

    secrets = jl(cred_row.get("secrets"), {}) or {}
    meta = jl(cred_row.get("meta"), {}) or {}
    password = secrets.get("password")
    email_addr = meta.get("email") or cred_row.get("account_id")
    host = meta.get("imap_host") or "imap.gmail.com"
    port = int(meta.get("imap_port") or 993)
    use_ssl = meta.get("use_ssl", True)
    ws_id = cred_row["workspace_id"]
    cred_id = cred_row["id"]
    if not password or not email_addr:
        return {"ingested": 0, "fetched": 0, "error": "imap creds incomplete"}

    def _fetch_unread() -> list[dict]:
        """Connect, SEARCH UNSEEN, FETCH each message body, mark as Seen."""
        import imaplib
        out: list[dict] = []
        imap_cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
        try:
            conn = imap_cls(host, port)
        except Exception as e:
            raise RuntimeError(f"imap connect failed: {e}")
        try:
            conn.login(email_addr, password)
            conn.select("INBOX")
            typ, data = conn.search(None, "UNSEEN")
            if typ != "OK":
                return out
            ids = (data[0] or b"").split()
            # Cap per-tick to avoid re-login churn if the inbox is huge.
            for i in ids[:25]:
                typ, msg_data = conn.fetch(i, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = message_from_bytes(raw)
                from_name, from_email = parseaddr(_decode(msg.get("From")))
                subject = _decode(msg.get("Subject"))
                msgid = (msg.get("Message-ID") or "").strip()
                body_text = _extract_body(msg)
                out.append({
                    "id": i.decode() if isinstance(i, bytes) else str(i),
                    "message_id": msgid or f"imap-{email_addr}-{i}",
                    "from_name": from_name or from_email.split("@", 1)[0] if from_email else "Mailbox",
                    "from_email": from_email,
                    "subject": subject,
                    "thread_id": msg.get("Thread-Topic") or msgid or subject,
                    "body": body_text,
                })
                # Mark as Seen so the next poll skips it.
                try:
                    conn.store(i, "+FLAGS", "\\Seen")
                except Exception:
                    pass
            conn.close()
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return out

    try:
        fetched = await asyncio.to_thread(_fetch_unread)
    except Exception as e:
        await db.execute(
            "UPDATE platform_credentials SET status = 'reauth_required' WHERE id = ?",
            (cred_id,),
        )
        await db.commit()
        return {"ingested": 0, "fetched": 0, "error": repr(e)[:200]}

    ingested = 0
    for mail in fetched:
        try:
            cust_id = await resolve_or_create_customer(
                workspace_id=ws_id,
                email=mail["from_email"] or None,
                name=mail["from_name"] or "Mailbox",
            )
            conv_id = await resolve_or_create_conversation(
                workspace_id=ws_id, customer_id=cust_id, channel="imap_smtp",
                external_thread_id=mail["thread_id"],
            )
            # Persist base subject on conversation so replies can re-use it.
            if mail["subject"]:
                import re
                base = re.sub(r"^\s*(re|fwd|fw)\s*:\s*", "", mail["subject"],
                              flags=re.IGNORECASE).strip()
                await db.execute(
                    """
                    UPDATE conversations SET subject = ?
                     WHERE id = ? AND (subject IS NULL OR subject = '')
                    """,
                    (base or mail["subject"], conv_id),
                )
                await db.commit()
            await ingest_inbound_message(
                workspace_id=ws_id, conversation_id=conv_id,
                text=(mail["body"] or mail["subject"] or "").strip(),
                from_name=mail["from_name"],
                external_message_id=mail["message_id"],
            )
            ingested += 1
        except Exception:
            log.exception("imap ingest failed")
            continue

    return {"ingested": ingested, "fetched": len(fetched)}


async def poll_all_imap_mailboxes(workspace_id: str | None = None) -> dict:
    db = await get_db()
    clauses = ["provider = 'imap_smtp'", "status = 'active'"]
    params: list = []
    if workspace_id:
        clauses.append("workspace_id = ?"); params.append(workspace_id)
    sql = f"SELECT * FROM platform_credentials WHERE {' AND '.join(clauses)} ORDER BY created_at"
    cur = await db.execute(sql, tuple(params))
    rows = [dict(r) for r in await cur.fetchall()]
    totals = {"accounts": 0, "ingested": 0, "fetched": 0, "errors": []}
    for r in rows:
        try:
            result = await poll_imap_for_credential(db, r)
        except Exception as e:
            log.exception("imap poller crashed for account %s", r.get("account_id"))
            result = {"ingested": 0, "fetched": 0, "error": repr(e)[:200]}
        totals["accounts"] += 1
        totals["ingested"] += int(result.get("ingested") or 0)
        totals["fetched"] += int(result.get("fetched") or 0)
        if result.get("error"):
            totals["errors"].append({"account": r.get("account_id"),
                                     "error": result["error"]})
    return totals
