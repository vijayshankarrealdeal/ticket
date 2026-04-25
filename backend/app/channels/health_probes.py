"""Credential liveness probes (spec §3.9.6 + §6).

Each probe is a cheap read-only call against the provider API to verify the
credentials actually work. The Channels page's "Test connection" button hits
`POST /platforms/:id/test` which dispatches in here. Probes return a uniform
`{ok, message, detail?}` so the UI can render a success/error badge without
branching per provider.

Probes SHOULD be read-only and idempotent. Don't send real messages, don't
mutate the provider's inbox — just fetch an account/profile descriptor.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def _http_get(url: str, *, headers: dict | None = None,
                     params: dict | None = None, timeout: float = 15) -> tuple[int, dict | str]:
    try:
        import httpx  # type: ignore
    except ImportError:
        return 0, "httpx not installed"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {}, params=params or {})
    except Exception as e:
        return 0, repr(e)
    try:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    except Exception:
        body = resp.text
    return resp.status_code, body


async def _probe_whatsapp(secrets: dict, meta: dict, account_id: str | None) -> dict:
    token = secrets.get("access_token")
    pnid = meta.get("phone_number_id") or account_id
    if not token or not pnid:
        return {"ok": False, "message": "phone_number_id + access_token required"}
    status, body = await _http_get(
        f"https://graph.facebook.com/v18.0/{pnid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if status == 200 and isinstance(body, dict):
        display = body.get("display_phone_number") or body.get("verified_name") or pnid
        return {"ok": True, "message": f"Connected to WhatsApp number {display}"}
    return {"ok": False, "message": f"HTTP {status}", "detail": str(body)[:300]}


async def _probe_instagram(secrets: dict, meta: dict, account_id: str | None) -> dict:
    token = secrets.get("access_token")
    page_id = meta.get("page_id") or account_id
    if not token or not page_id:
        return {"ok": False, "message": "page_id + access_token required"}
    status, body = await _http_get(
        f"https://graph.facebook.com/v18.0/{page_id}",
        params={"fields": "name,username", "access_token": token},
    )
    if status == 200 and isinstance(body, dict):
        return {"ok": True, "message": f"Connected to page {body.get('name') or page_id}"}
    return {"ok": False, "message": f"HTTP {status}", "detail": str(body)[:300]}


async def _probe_messenger(secrets: dict, meta: dict, account_id: str | None) -> dict:
    token = secrets.get("page_access_token")
    page_id = meta.get("page_id") or account_id
    if not token or not page_id:
        return {"ok": False, "message": "page_id + page_access_token required"}
    status, body = await _http_get(
        f"https://graph.facebook.com/v18.0/{page_id}",
        params={"fields": "name", "access_token": token},
    )
    if status == 200 and isinstance(body, dict):
        return {"ok": True, "message": f"Connected to page {body.get('name') or page_id}"}
    return {"ok": False, "message": f"HTTP {status}", "detail": str(body)[:300]}


async def _probe_telegram(secrets: dict, meta: dict, account_id: str | None) -> dict:
    token = secrets.get("bot_token")
    if not token:
        return {"ok": False, "message": "bot_token required"}
    status, body = await _http_get(f"https://api.telegram.org/bot{token}/getMe")
    if status == 200 and isinstance(body, dict) and body.get("ok"):
        bot = body.get("result") or {}
        return {"ok": True,
                "message": f"Connected to @{bot.get('username') or bot.get('first_name')}"}
    return {"ok": False, "message": f"HTTP {status}", "detail": str(body)[:300]}


async def _probe_twilio(secrets: dict, meta: dict, account_id: str | None) -> dict:
    sid = meta.get("account_sid") or account_id
    tok = secrets.get("auth_token")
    if not sid or not tok:
        return {"ok": False, "message": "account_sid + auth_token required"}
    try:
        import httpx  # type: ignore
    except ImportError:
        return {"ok": False, "message": "httpx not installed"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                auth=(sid, tok),
            )
    except Exception as e:
        return {"ok": False, "message": repr(e)[:200]}
    if resp.status_code == 200:
        j = {}
        try:
            j = resp.json()
        except Exception:
            pass
        return {"ok": True,
                "message": f"Connected to Twilio account {j.get('friendly_name') or sid}"}
    return {"ok": False, "message": f"HTTP {resp.status_code}",
            "detail": resp.text[:300]}


async def _probe_imap_smtp(secrets: dict, meta: dict, account_id: str | None) -> dict:
    import asyncio
    password = secrets.get("password")
    email = meta.get("email") or account_id
    host = meta.get("imap_host") or "imap.gmail.com"
    port = int(meta.get("imap_port") or 993)
    use_ssl = meta.get("use_ssl", True)
    if not password or not email:
        return {"ok": False, "message": "email + password required"}

    def _check() -> str:
        import imaplib
        cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
        conn = cls(host, port)
        try:
            conn.login(email, password)
            conn.select("INBOX")
        finally:
            try: conn.logout()
            except Exception: pass
        return "ok"

    try:
        await asyncio.to_thread(_check)
    except Exception as e:
        return {"ok": False, "message": repr(e)[:200]}
    return {"ok": True, "message": f"Logged in to {email} on {host}:{port}"}


async def _probe_shopify(secrets: dict, meta: dict, account_id: str | None) -> dict:
    shop = meta.get("shop_domain") or account_id
    token = secrets.get("admin_access_token")
    if not shop or not token:
        return {"ok": False, "message": "shop_domain + admin_access_token required"}
    status, body = await _http_get(
        f"https://{shop}/admin/api/2024-04/shop.json",
        headers={"X-Shopify-Access-Token": token},
    )
    if status == 200 and isinstance(body, dict):
        s = body.get("shop") or {}
        return {"ok": True, "message": f"Connected to {s.get('name') or shop}"}
    return {"ok": False, "message": f"HTTP {status}", "detail": str(body)[:300]}


async def _probe_woocommerce(secrets: dict, meta: dict, account_id: str | None) -> dict:
    site = meta.get("site_url") or account_id
    key = meta.get("consumer_key")
    sec = secrets.get("consumer_secret")
    if not site or not key or not sec:
        return {"ok": False, "message": "site_url + consumer_key + consumer_secret required"}
    try:
        import httpx  # type: ignore
    except ImportError:
        return {"ok": False, "message": "httpx not installed"}
    url = f"{site.rstrip('/')}/wp-json/wc/v3/system_status"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, auth=(key, sec))
    except Exception as e:
        return {"ok": False, "message": repr(e)[:200]}
    if resp.status_code == 200:
        return {"ok": True, "message": f"WooCommerce REST API reachable at {site}"}
    return {"ok": False, "message": f"HTTP {resp.status_code}",
            "detail": resp.text[:300]}


PROBES = {
    "whatsapp": _probe_whatsapp,
    "instagram": _probe_instagram,
    "messenger": _probe_messenger,
    "telegram": _probe_telegram,
    "twilio": _probe_twilio,
    "imap_smtp": _probe_imap_smtp,
    "shopify": _probe_shopify,
    "woocommerce": _probe_woocommerce,
}


async def probe(provider: str, *, secrets: dict, meta: dict,
                account_id: str | None) -> dict:
    fn = PROBES.get(provider)
    if not fn:
        return {"ok": True, "message": f"No liveness probe for {provider} — saved."}
    try:
        return await fn(secrets or {}, meta or {}, account_id)
    except Exception as e:
        log.exception("probe %s crashed", provider)
        return {"ok": False, "message": repr(e)[:200]}
