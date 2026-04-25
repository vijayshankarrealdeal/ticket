"""Connector credential schemas (spec §6).

Every non-OAuth channel card on the Channels page renders a credential form.
The form fields are declared here once and served to the UI via
`GET /platforms/:id/schema` so the SPA doesn't have to hard-code what each
provider needs. `POST /platforms/:id/configure` validates the submitted body
against this registry and writes the row into `platform_credentials`.

The goal is to let a workspace admin drop in API keys from the provider's
console without editing `.env` — the adapters in [worker.py](../worker.py) read
`platform_credentials` first, env vars only as a fallback.

Docs links are surfaced in the modal as "Learn more"; they point at the
provider's own key-provisioning page so the admin can grab the values in one
tab and paste them in the next.
"""
from __future__ import annotations

from typing import Any


# Each entry: provider_id → {
#   provider: the `platform_credentials.provider` slug the adapters expect,
#   account_field: which field becomes `account_id` (uniqueness key),
#   secrets: which fields go into `secrets` (encrypted blob),
#   meta: which fields go into `meta` (non-secret scaffolding),
#   fields: form field schema for the UI,
#   docs_url: provider docs link,
#   overview: two-line description shown at the top of the modal,
#   test_endpoint: optional — if set, the backend pings it after save to
#                  verify credentials before flipping the card green.
# }
CONNECTORS: dict[str, dict[str, Any]] = {
    # ---------- Messaging ----------
    "whatsapp": {
        "provider": "whatsapp",
        "account_field": "phone_number_id",
        "secrets": ["access_token", "app_secret"],
        "meta": ["phone_number_id", "business_account_id", "verify_token", "display_phone"],
        "docs_url": "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started",
        "overview": (
            "Create a WhatsApp Business app in Meta for Developers, add a "
            "phone number, and copy the values below from the app dashboard."
        ),
        "webhook_instructions": (
            "Set the app's webhook URL to `{backend}/webhooks/whatsapp` and "
            "use the Verify Token you entered above."
        ),
        "fields": [
            {"key": "phone_number_id", "label": "Phone number ID", "required": True,
             "placeholder": "105954345678910",
             "help": "From the WhatsApp → API setup page in Meta Business Manager."},
            {"key": "business_account_id", "label": "WhatsApp Business Account ID",
             "required": False, "placeholder": "100482345678910",
             "help": "Optional — needed for template sending later on."},
            {"key": "access_token", "label": "Permanent access token", "required": True,
             "secret": True, "placeholder": "EAAG…",
             "help": "System-user token with `whatsapp_business_messaging` scope."},
            {"key": "verify_token", "label": "Webhook verify token", "required": True,
             "placeholder": "a-random-string-you-choose",
             "help": "You enter this same value when registering the webhook in Meta."},
            {"key": "app_secret", "label": "App secret (optional)", "required": False,
             "secret": True,
             "help": "Used to validate X-Hub-Signature-256 on inbound webhooks."},
            {"key": "display_phone", "label": "Display phone number", "required": False,
             "placeholder": "+1 555 010 4567"},
        ],
    },
    "instagram": {
        "provider": "instagram",
        "account_field": "page_id",
        "secrets": ["access_token", "app_secret"],
        "meta": ["page_id", "ig_user_id", "verify_token", "handle"],
        "docs_url": "https://developers.facebook.com/docs/messenger-platform/instagram",
        "overview": (
            "Connect an Instagram Business account linked to a Facebook Page. "
            "Generate a page access token with `instagram_manage_messages` scope."
        ),
        "webhook_instructions": (
            "In the Meta app dashboard, add Messenger → Instagram settings "
            "and point the callback URL at `{backend}/webhooks/instagram`."
        ),
        "fields": [
            {"key": "page_id", "label": "Facebook Page ID", "required": True,
             "placeholder": "102345678901234"},
            {"key": "ig_user_id", "label": "Instagram user ID", "required": False,
             "placeholder": "17841405793187218"},
            {"key": "handle", "label": "Instagram handle", "required": False,
             "placeholder": "@yourbrand"},
            {"key": "access_token", "label": "Page access token", "required": True,
             "secret": True,
             "help": "`pages_messaging` + `instagram_manage_messages` scopes."},
            {"key": "verify_token", "label": "Webhook verify token", "required": True,
             "placeholder": "a-random-string-you-choose"},
            {"key": "app_secret", "label": "App secret (optional)", "required": False,
             "secret": True},
        ],
    },
    "messenger": {
        "provider": "messenger",
        "account_field": "page_id",
        "secrets": ["page_access_token", "app_secret"],
        "meta": ["page_id", "verify_token", "page_name"],
        "docs_url": "https://developers.facebook.com/docs/messenger-platform/getting-started",
        "overview": (
            "Add Messenger to your Meta app, subscribe your Page to `messages` "
            "and `messaging_postbacks`, then paste the tokens below."
        ),
        "webhook_instructions": (
            "Point the Messenger webhook at `{backend}/webhooks/messenger`."
        ),
        "fields": [
            {"key": "page_id", "label": "Facebook Page ID", "required": True,
             "placeholder": "102345678901234"},
            {"key": "page_name", "label": "Page name", "required": False,
             "placeholder": "Acme Support"},
            {"key": "page_access_token", "label": "Page access token",
             "required": True, "secret": True,
             "help": "Generated under Messenger → Settings → Access Tokens."},
            {"key": "verify_token", "label": "Webhook verify token", "required": True,
             "placeholder": "a-random-string-you-choose"},
            {"key": "app_secret", "label": "App secret (optional)", "required": False,
             "secret": True},
        ],
    },
    "telegram": {
        "provider": "telegram",
        "account_field": "bot_username",
        "secrets": ["bot_token"],
        "meta": ["bot_username", "bot_id", "use_webhook", "webhook_secret"],
        "docs_url": "https://core.telegram.org/bots/features#botfather",
        "overview": (
            "Talk to @BotFather in Telegram, run /newbot, and paste the HTTP "
            "API token here. We'll poll for messages — no public URL needed."
        ),
        "fields": [
            {"key": "bot_token", "label": "Bot token", "required": True, "secret": True,
             "placeholder": "1234567890:AA…",
             "help": "BotFather returns this right after you create the bot."},
            {"key": "bot_username", "label": "Bot username (optional)",
             "required": False, "placeholder": "my_support_bot"},
            {"key": "webhook_secret", "label": "Webhook secret (optional)",
             "required": False, "secret": True,
             "help": "Set this if you prefer a webhook over polling."},
        ],
    },
    "sms-twilio": {
        "provider": "twilio",
        "account_field": "account_sid",
        "secrets": ["auth_token"],
        "meta": ["account_sid", "from", "messaging_service_sid"],
        "docs_url": "https://www.twilio.com/docs/sms/quickstart",
        "overview": (
            "Find your Account SID, Auth Token and SMS-enabled number on the "
            "Twilio console. We'll use them to send and receive messages."
        ),
        "webhook_instructions": (
            "In the Twilio console, point your phone number's SMS webhook at "
            "`{backend}/webhooks/twilio/sms` (HTTP POST)."
        ),
        "fields": [
            {"key": "account_sid", "label": "Account SID", "required": True,
             "placeholder": "AC…"},
            {"key": "auth_token", "label": "Auth token", "required": True, "secret": True,
             "placeholder": "your auth token"},
            {"key": "from", "label": "From number (E.164)", "required": True,
             "placeholder": "+14155550123"},
            {"key": "messaging_service_sid",
             "label": "Messaging service SID (optional)", "required": False,
             "placeholder": "MG…"},
        ],
    },
    "imap-smtp": {
        "provider": "imap_smtp",
        "account_field": "email",
        "secrets": ["password"],
        "meta": ["email", "imap_host", "imap_port", "smtp_host", "smtp_port", "use_ssl"],
        "docs_url": "https://support.google.com/mail/answer/185833",
        "overview": (
            "Connect any mailbox using standard IMAP + SMTP. For Gmail create "
            "an App Password; for Outlook use a Microsoft App Password."
        ),
        "fields": [
            {"key": "email", "label": "Email address", "required": True,
             "placeholder": "support@yourcompany.com"},
            {"key": "password", "label": "App password / mailbox password",
             "required": True, "secret": True},
            {"key": "imap_host", "label": "IMAP host", "required": True,
             "placeholder": "imap.gmail.com", "default": "imap.gmail.com"},
            {"key": "imap_port", "label": "IMAP port", "required": True,
             "type": "number", "default": 993},
            {"key": "smtp_host", "label": "SMTP host", "required": True,
             "placeholder": "smtp.gmail.com", "default": "smtp.gmail.com"},
            {"key": "smtp_port", "label": "SMTP port", "required": True,
             "type": "number", "default": 587},
            {"key": "use_ssl", "label": "Use SSL/TLS", "required": False,
             "type": "boolean", "default": True},
        ],
    },
    "live-chat": {
        "provider": "live_chat",
        "account_field": "workspace_slug",
        "secrets": [],
        "meta": ["workspace_slug", "greeting", "accent_color", "proactive_ms"],
        "docs_url": "/channels/live-chat",
        "overview": (
            "Paste the snippet below into your site's <head>. Visitors get a "
            "launcher bubble that opens a chat we route to Conversations."
        ),
        "fields": [
            {"key": "greeting", "label": "Greeting message", "required": False,
             "placeholder": "👋 Hi — how can we help today?",
             "default": "👋 Hi — how can we help today?"},
            {"key": "accent_color", "label": "Accent color", "required": False,
             "placeholder": "#2563eb", "default": "#2563eb"},
            {"key": "proactive_ms",
             "label": "Proactive prompt delay (ms)", "required": False,
             "type": "number", "default": 0,
             "help": "0 disables. Set to 20000 for a 20-second idle prompt."},
        ],
    },
    # ---------- E-commerce ----------
    "shopify": {
        "provider": "shopify",
        "account_field": "shop_domain",
        "secrets": ["admin_access_token", "api_secret_key"],
        "meta": ["shop_domain"],
        "docs_url": "https://shopify.dev/docs/apps/auth/admin-app-access-tokens",
        "overview": (
            "Install a custom app on your Shopify store with `read_orders` + "
            "`read_customers` scopes and paste the Admin API token here."
        ),
        "fields": [
            {"key": "shop_domain", "label": "Shop domain", "required": True,
             "placeholder": "yourstore.myshopify.com"},
            {"key": "admin_access_token", "label": "Admin API access token",
             "required": True, "secret": True,
             "placeholder": "shpat_…"},
            {"key": "api_secret_key",
             "label": "API secret key (webhook HMAC)", "required": False,
             "secret": True},
        ],
    },
    "woocommerce": {
        "provider": "woocommerce",
        "account_field": "site_url",
        "secrets": ["consumer_secret"],
        "meta": ["site_url", "consumer_key"],
        "docs_url": "https://woocommerce.com/document/woocommerce-rest-api/",
        "overview": (
            "Generate a REST API key pair in WooCommerce → Settings → "
            "Advanced → REST API with Read/Write permission."
        ),
        "fields": [
            {"key": "site_url", "label": "WooCommerce site URL", "required": True,
             "placeholder": "https://store.example.com"},
            {"key": "consumer_key", "label": "Consumer key", "required": True,
             "placeholder": "ck_…"},
            {"key": "consumer_secret", "label": "Consumer secret", "required": True,
             "secret": True, "placeholder": "cs_…"},
        ],
    },
    # ---------- Shipping ----------
    "shiprocket": {
        "provider": "shiprocket",
        "account_field": "email",
        "secrets": ["password", "api_token"],
        "meta": ["email"],
        "docs_url": "https://apidocs.shiprocket.in/",
        "overview": (
            "Provide your Shiprocket dashboard login. We exchange it for an "
            "API token and refresh every 9 days."
        ),
        "fields": [
            {"key": "email", "label": "Shiprocket email", "required": True,
             "placeholder": "ops@yourbrand.com"},
            {"key": "password", "label": "Shiprocket password", "required": True,
             "secret": True},
        ],
    },
    "delhivery": {
        "provider": "delhivery",
        "account_field": "api_token",
        "secrets": ["api_token"],
        "meta": ["client_name"],
        "docs_url": "https://track.delhivery.com/api/docs",
        "overview": "Paste the API token from your Delhivery partner portal.",
        "fields": [
            {"key": "api_token", "label": "Delhivery API token", "required": True,
             "secret": True},
            {"key": "client_name", "label": "Client name", "required": False},
        ],
    },
    "bluedart": {
        "provider": "bluedart",
        "account_field": "login_id",
        "secrets": ["license_key"],
        "meta": ["login_id", "customer_code"],
        "docs_url": "https://www.bluedart.com/api",
        "overview": "Enter your Blue Dart API credentials.",
        "fields": [
            {"key": "login_id", "label": "Login ID", "required": True},
            {"key": "license_key", "label": "License key", "required": True,
             "secret": True},
            {"key": "customer_code", "label": "Customer code", "required": False},
        ],
    },
    "fedex": {
        "provider": "fedex",
        "account_field": "account_number",
        "secrets": ["api_secret"],
        "meta": ["api_key", "account_number"],
        "docs_url": "https://developer.fedex.com/api/en-us/catalog.html",
        "overview": "Create an API project in the FedEx developer portal.",
        "fields": [
            {"key": "api_key", "label": "API key", "required": True},
            {"key": "api_secret", "label": "API secret", "required": True,
             "secret": True},
            {"key": "account_number", "label": "Account number", "required": True},
        ],
    },
    "dhl": {
        "provider": "dhl",
        "account_field": "api_key",
        "secrets": ["api_secret"],
        "meta": ["api_key"],
        "docs_url": "https://developer.dhl.com/",
        "overview": "Generate a DHL Express MyAPI key.",
        "fields": [
            {"key": "api_key", "label": "API key", "required": True},
            {"key": "api_secret", "label": "API secret", "required": True,
             "secret": True},
        ],
    },
    "amazon": {
        "provider": "amazon",
        "account_field": "seller_id",
        "secrets": ["refresh_token", "lwa_client_secret"],
        "meta": ["seller_id", "marketplace_id", "lwa_client_id", "region"],
        "docs_url": "https://developer-docs.amazon.com/sp-api/docs/welcome",
        "overview": (
            "Authorise a self-hosted Selling Partner app and paste the LWA "
            "refresh token below."
        ),
        "fields": [
            {"key": "seller_id", "label": "Seller ID", "required": True},
            {"key": "marketplace_id", "label": "Marketplace ID", "required": True,
             "placeholder": "ATVPDKIKX0DER"},
            {"key": "region", "label": "Region", "required": False,
             "placeholder": "us-east-1", "default": "us-east-1"},
            {"key": "lwa_client_id", "label": "LWA client ID", "required": True},
            {"key": "lwa_client_secret", "label": "LWA client secret",
             "required": True, "secret": True},
            {"key": "refresh_token", "label": "LWA refresh token", "required": True,
             "secret": True},
        ],
    },
    "flipkart": {
        "provider": "flipkart",
        "account_field": "app_id",
        "secrets": ["app_secret"],
        "meta": ["app_id"],
        "docs_url": "https://seller.flipkart.com/api-docs/",
        "overview": "Use your Flipkart seller app OAuth credentials.",
        "fields": [
            {"key": "app_id", "label": "App ID", "required": True},
            {"key": "app_secret", "label": "App secret", "required": True,
             "secret": True},
        ],
    },
    "magento": {
        "provider": "magento",
        "account_field": "site_url",
        "secrets": ["access_token"],
        "meta": ["site_url"],
        "docs_url": "https://developer.adobe.com/commerce/webapi/rest/",
        "overview": "Create an integration token in Magento → System → Integrations.",
        "fields": [
            {"key": "site_url", "label": "Magento base URL", "required": True,
             "placeholder": "https://store.example.com"},
            {"key": "access_token", "label": "Integration access token",
             "required": True, "secret": True},
        ],
    },
}


def schema_for(platform_id: str) -> dict | None:
    """Return a UI-safe schema for this platform id (no secret values)."""
    cfg = CONNECTORS.get(platform_id)
    if not cfg:
        return None
    return {
        "id": platform_id,
        "provider": cfg["provider"],
        "docs_url": cfg.get("docs_url"),
        "overview": cfg.get("overview"),
        "webhook_instructions": cfg.get("webhook_instructions"),
        "fields": cfg.get("fields") or [],
    }


def split_payload(platform_id: str, values: dict[str, Any]) -> tuple[dict, dict, str | None]:
    """Partition the submitted form values into (secrets, meta, account_id).

    - `secrets` — encrypted blob fields (tokens, passwords).
    - `meta`    — non-secret scaffolding the adapters read.
    - `account_id` — provider-scoped uniqueness key (phone_number_id, shop, …).
    """
    cfg = CONNECTORS.get(platform_id) or {}
    secret_keys = set(cfg.get("secrets") or [])
    meta_keys = set(cfg.get("meta") or [])
    secrets = {k: values[k] for k in secret_keys if k in values and values[k] not in (None, "")}
    meta = {k: values[k] for k in meta_keys if k in values and values[k] not in (None, "")}
    account_id = None
    ac = cfg.get("account_field")
    if ac:
        account_id = values.get(ac) or meta.get(ac) or secrets.get(ac)
    return secrets, meta, (str(account_id) if account_id else None)


def validate_values(platform_id: str, values: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors — empty list when OK."""
    cfg = CONNECTORS.get(platform_id)
    if not cfg:
        return [f"Unknown platform: {platform_id}"]
    errors: list[str] = []
    for f in cfg.get("fields") or []:
        if not f.get("required"):
            continue
        v = values.get(f["key"])
        if v is None or v == "":
            errors.append(f"{f.get('label') or f['key']} is required")
    return errors


def mask_secret(value: str | None) -> str | None:
    """Render a stored secret as `••••abcd` so the Manage modal can show
    "something is set" without leaking the token."""
    if not value or not isinstance(value, str):
        return None
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
