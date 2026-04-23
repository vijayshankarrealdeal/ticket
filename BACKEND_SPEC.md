# Backend Spec — WorkspaceX (AI-first ticket handling)

> Source: `src/` of this repo, as of 2026-04-22. All data on the frontend is hardcoded mocks today; every item below is what the UI **renders** and **expects to read/write**. Use this as a checklist — tick what's done, add what's missing.

---

## 1. What the app is

WorkspaceX is an **AI-first omnichannel support desk** for a D2C brand (The Sleep Company is the seeded workspace).

**Intended flow**
1. A customer reaches out over a channel — WhatsApp, Instagram DM, Live Chat (web widget), or Email (Gmail/Outlook).
2. The system ingests the message, finds/creates a **Customer** record (by phone / email / Shopify ID), and groups messages into a **Conversation**.
3. An **AI agent** (configured in Agent Builder or in the backend system) tries to answer directly using tools: KB search, order lookup, shipping status, refund (bounded), etc.
4. If the AI can't resolve it, the message is promoted into a **Ticket** with: `status`, `source`, `task` (objective), `team`, `assignee`, and SLA. Tickets can **block on a child ticket** (e.g. a logistics sub-task that must be answered before the parent progresses).
5. **Admins / agents** pick it up from the Tickets inbox, coordinate in **Teams** (internal Slack-style chat), and resolve it — persisting `VOC`, resolution notes, timeline, and custom fields.
6. Analytics, notifications, platform/channel configuration, and health monitoring surround all of the above.

The frontend is a single-page React app (Vite, React Router). Auth is currently a localStorage stub (`src/pages/AuthPage.jsx`). Every API call below must replace the hardcoded arrays in each page file.

---

## 2. Cross-cutting concerns (do these once)

- [ ] **Auth**: replace `localStorage` stub in [src/pages/AuthPage.jsx](src/pages/AuthPage.jsx). Needs: `POST /auth/signup`, `POST /auth/signin`, `POST /auth/signout`, Google/Outlook OAuth, `POST /auth/forgot-password`. Session user shape: `{ name, email, role, provider, workspaceId, signedInAt }`.
- [ ] **RBAC**: Settings → Profile exposes roles `admin | agent | viewer`. Enforce on every endpoint.
- [ ] **Workspace / multi-tenant**: the TopBar and Settings show a single workspace ("The Sleep Company"). Scope every record by `workspace_id`.
- [ ] **Realtime transport**: the inbox, teams chat, notifications and ticket updates all need push — WebSocket or SSE. Events needed: `message.created`, `conversation.updated`, `ticket.updated`, `ticket.assigned`, `notification.created`, `team.message.created`, `team.reaction.changed`, `typing.started/stopped`, `sla.breach_warning`, `webhook.failed`.
- [ ] **File storage**: presigned-URL upload for images / docs attached in chat (see Conversations). Return a stable CDN URL + size + mime.
- [ ] **Global search**: [src/data/globalSearchData.js](src/data/globalSearchData.js) shows the expected ranked groups — conversations, tickets, contacts. Needs a single `GET /search?q=&limit=` that returns `{ conversations, tickets, contacts, total }`. Ideally backed by a search index (OpenSearch / Postgres trigram / Meili).
- [ ] **Event log / audit trail**: the Ticket detail renders a per-ticket `timeline`. Every state change must emit a timeline entry with `at`, `actor` (user or "system"), and `text`.
- [ ] **Idempotency & tracing**: the API Logs UI shows per-call `trace_id`, `customer_id`, `latency`, status codes. Mirror that on real endpoints.

---

## 3. Page-by-page spec

Each page lists **what it shows / does** (so the UX contract is clear), followed by the **endpoints / data it needs**. Tick boxes are for backend team to track implementation.

---

### 3.1 Auth — [src/pages/AuthPage.jsx](src/pages/AuthPage.jsx)
**UI**: split-screen hero + form; sign in / sign up toggle; password strength meter; Google & Outlook SSO buttons; forgot-password link (stub); theme toggle.

Backend endpoints:
- [ ] `POST /auth/signup` → `{ name, email, password }` → `{ user, token }`
- [ ] `POST /auth/signin` → `{ email, password }` → `{ user, token }`
- [ ] `GET /auth/oauth/google/start` → 302 to Google consent
- [ ] `GET /auth/oauth/google/callback` → issues session cookie
- [ ] `GET /auth/oauth/outlook/start` → 302 to Microsoft consent
- [ ] `GET /auth/oauth/outlook/callback` → issues session cookie
- [ ] `POST /auth/forgot-password` → email magic link
- [ ] `POST /auth/signout`
- [ ] `GET /auth/me` to rehydrate session

#### Password auth

1. **Signup**: validate email format + password strength (≥ 8 chars, done client-side in `AuthPage`; re-validate server-side). Hash password with **Argon2id** (`memory=64MB, time=3, parallelism=4`). Insert a `users` row and a `workspaces` row if this is the first user of an org. Return a session.
2. **Signin**: look up user by `email`, verify Argon2id hash, create `auth_sessions` row with a cryptographically random token (32 bytes, base64url), return it as an **httpOnly, Secure, SameSite=Lax** cookie `wx_session=<token>` with 30-day expiry (sliding).
3. **Session model**: server stores `sha256(token)` only; never the raw token. Rehydrate on `GET /auth/me` by SHA-256 hashing the cookie and looking up `auth_sessions.token_hash`.
4. **Forgot password**: generate a one-time token, store `sha256(token)` in a `password_resets` table with 30-min expiry, email the link `https://app.workspacex.com/reset?token=...`. Reset endpoint wipes the row on use.
5. **2FA** (Security settings): TOTP via `otplib`. Store `secret_enc` with `pgp_sym_encrypt`. Verify with ±1 window.

#### Google SSO — step-by-step

**One-time setup (GCP Console)**:
1. Create project "WorkspaceX".
2. **APIs & Services → OAuth consent screen**:
   - User type: *External* (or *Internal* for Google Workspace domain).
   - App name, support email, logo, authorized domain `workspacex.com`.
   - Scopes: `openid`, `email`, `profile`.
   - Add test users while in "Testing" mode; submit for verification before "In production".
3. **APIs & Services → Credentials → Create OAuth client ID**:
   - Application type: *Web application*.
   - Authorized JavaScript origins: `https://app.workspacex.com`.
   - Authorized redirect URIs: `https://api.workspacex.com/auth/oauth/google/callback` (and a `http://localhost:3000/...` for dev).
4. Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to Secrets Manager.

**Frontend click** — the `onClick={handleSso('google')}` button in [src/pages/AuthPage.jsx:288](src/pages/AuthPage.jsx#L288) currently simulates a `setTimeout`. Replace with:
```js
window.location.href = '/auth/oauth/google/start?return=' + encodeURIComponent(window.location.pathname);
```

**Backend flow**:

1. `GET /auth/oauth/google/start`:
   - Generate `state` = random 32-byte base64url and `nonce` = another 32 bytes. Store in a short-lived Redis key `oauth:google:<state>` (TTL 10 min) with `{ return, nonce, pkce_verifier }`.
   - Compute `code_challenge = base64url(sha256(pkce_verifier))`.
   - 302 to:
     ```
     https://accounts.google.com/o/oauth2/v2/auth
       ?client_id=$GOOGLE_CLIENT_ID
       &redirect_uri=https://api.workspacex.com/auth/oauth/google/callback
       &response_type=code
       &scope=openid%20email%20profile
       &state=<state>
       &nonce=<nonce>
       &code_challenge=<code_challenge>
       &code_challenge_method=S256
       &access_type=online        // 'offline' only if you need a refresh_token (not needed for sign-in)
       &prompt=select_account
     ```

2. User accepts → Google redirects to `GET /auth/oauth/google/callback?code=...&state=...`.

3. `GET /auth/oauth/google/callback`:
   - Look up Redis `oauth:google:<state>`. If missing → 400 (CSRF / replay).
   - `POST https://oauth2.googleapis.com/token` (form-urlencoded):
     ```
     code=<code>
     client_id=$GOOGLE_CLIENT_ID
     client_secret=$GOOGLE_CLIENT_SECRET
     redirect_uri=https://api.workspacex.com/auth/oauth/google/callback
     grant_type=authorization_code
     code_verifier=<pkce_verifier>
     ```
     → `{ access_token, id_token, expires_in, scope, token_type }`.
   - **Verify the `id_token`** (JWT) against Google's JWKS (`https://www.googleapis.com/oauth2/v3/certs`). Check `iss` ∈ `{accounts.google.com, https://accounts.google.com}`, `aud == $GOOGLE_CLIENT_ID`, `exp > now`, `nonce == stored nonce`, `email_verified == true`.
   - Claims you use: `sub` (Google user id, stable), `email`, `name`, `picture`.
   - **User upsert**:
     ```sql
     SELECT id FROM users
       WHERE workspace_id = $ws AND (provider = 'google' AND oauth_sub = $sub OR email = $email);
     ```
     - If found with `provider='password'`: link the account (set `oauth_sub`, keep password). Return existing user.
     - If not found: create user `{ name, email, provider='google', oauth_sub=sub, password_hash=NULL, role='agent' }`. If first user of org → also create `workspaces` row.
   - Create `auth_sessions` row, set the `wx_session` cookie, 302 to `<return>` (default `/`).
   - Delete the Redis state key.

**Security guardrails**:
- Require `state` to match (CSRF).
- Require `nonce` inside the ID token to match (replay).
- Enforce `email_verified` — otherwise an attacker with an unverified Google account can claim any email.
- Do **not** trust `email` without `sub` — rely on `sub` as the primary identity.
- Log every callback with `trace_id`; alert on repeated failures.

#### Outlook / Microsoft SSO — step-by-step

**One-time setup (Azure portal)**:
1. **Entra ID → App registrations → New registration**:
   - Name: WorkspaceX.
   - Supported account types: *Accounts in any organizational directory and personal Microsoft accounts* (= `common` tenant).
   - Redirect URI (Web): `https://api.workspacex.com/auth/oauth/outlook/callback`.
2. Copy **Application (client) ID** → `OUTLOOK_CLIENT_ID`.
3. **Certificates & secrets → New client secret** → copy value → `OUTLOOK_CLIENT_SECRET`.
4. **API permissions → Add permission → Microsoft Graph → Delegated**: `openid`, `email`, `profile`, `User.Read`, `offline_access`. Click *Grant admin consent* (for your tenant) or rely on per-user consent.

**Frontend click** — same as Google, the Outlook SSO button in [src/pages/AuthPage.jsx:273](src/pages/AuthPage.jsx#L273) navigates to `/auth/oauth/outlook/start`.

**Backend flow**:

1. `GET /auth/oauth/outlook/start`:
   - Same state/nonce/PKCE pattern as Google. Key: `oauth:outlook:<state>`.
   - 302 to:
     ```
     https://login.microsoftonline.com/common/oauth2/v2.0/authorize
       ?client_id=$OUTLOOK_CLIENT_ID
       &response_type=code
       &redirect_uri=https://api.workspacex.com/auth/oauth/outlook/callback
       &response_mode=query
       &scope=openid%20email%20profile%20offline_access%20User.Read
       &state=<state>
       &nonce=<nonce>
       &code_challenge=<code_challenge>
       &code_challenge_method=S256
       &prompt=select_account
     ```
   - `common` = accept both personal and work accounts. Use `organizations` to block personal Microsoft accounts, or a specific tenant GUID to lock to one org.

2. Microsoft redirects to `GET /auth/oauth/outlook/callback?code=...&state=...`.

3. `GET /auth/oauth/outlook/callback`:
   - Look up Redis state.
   - `POST https://login.microsoftonline.com/common/oauth2/v2.0/token` (form-urlencoded):
     ```
     client_id=$OUTLOOK_CLIENT_ID
     client_secret=$OUTLOOK_CLIENT_SECRET
     grant_type=authorization_code
     code=<code>
     redirect_uri=https://api.workspacex.com/auth/oauth/outlook/callback
     code_verifier=<pkce_verifier>
     scope=openid email profile offline_access User.Read
     ```
     → `{ access_token, refresh_token, id_token, expires_in }`.
   - **Verify the `id_token`** against the Microsoft JWKS. For the `common` endpoint the JWKS URI is dynamic per tenant:
     - Fetch `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration` → take `jwks_uri`.
     - Validate `iss == https://login.microsoftonline.com/<tid>/v2.0` where `<tid>` is the `tid` claim from the ID token.
     - Check `aud == $OUTLOOK_CLIENT_ID`, `exp > now`, `nonce == stored`.
   - Useful claims: `oid` (object id, stable per tenant + user), `tid` (tenant id), `preferred_username` / `email`, `name`.
     - Primary identity key: `oid + tid` (or `sub` — which is tenant-scoped and also stable).
     - `email` may be empty for personal MSA; fall back to calling `GET https://graph.microsoft.com/v1.0/me` with the access token to get `mail` or `userPrincipalName`.
   - **User upsert**: same pattern as Google but `provider='outlook'` and `oauth_sub = oid + ':' + tid`.
   - Store `refresh_token` in `auth_sessions.refresh_enc` if you want silent re-auth later. For pure sign-in you can discard it.
   - Create session cookie, 302 to `<return>`.

**Edge cases**:
- Personal Microsoft accounts (`outlook.com`, `hotmail.com`) use `tid = 9188040d-6c67-4c5b-b112-36a304b66dad` — accept this if you chose `common`.
- If you later want this same token to read mail (§6.2), re-initiate with `Mail.Read Mail.Send` scopes — sign-in scopes are intentionally minimal.
- Tenant admins may disable user consent; if `AADSTS65001` surfaces, the admin must grant tenant consent via the admin URL.

#### Schema additions for OAuth

Add these columns to the `users` table in §7:
```sql
ALTER TABLE users
  ADD COLUMN oauth_sub text,
  ADD COLUMN avatar_url text;
CREATE UNIQUE INDEX users_provider_sub_idx ON users (workspace_id, provider, oauth_sub) WHERE oauth_sub IS NOT NULL;
```

`auth_sessions` stays the same; OAuth and password logins produce identical sessions.

#### Linking, unlinking, and account collisions

- **Same email, different provider**: the upsert logic above **links** automatically when `email` matches and `email_verified` is true. If you want stricter behavior, show a "Continue with Google to link?" screen on the next sign-in.
- **Unlink**: settings page (not yet in UI) should call `DELETE /me/oauth/:provider` — only if `password_hash IS NOT NULL` (don't lock the user out).
- **Sign-out**: `POST /auth/signout` deletes the `auth_sessions` row and clears the cookie. Optionally call `https://oauth2.googleapis.com/revoke?token=...` / `https://login.microsoftonline.com/common/oauth2/v2.0/logout` for full federation logout — usually overkill.

---

### 3.2 Sidebar / TopBar — [src/components/Sidebar.jsx](src/components/Sidebar.jsx), [src/components/TopBar.jsx](src/components/TopBar.jsx)
**UI**: nav items (Inbox, Tickets, Customers, Teams, Notifications, Insights, Make Agents, Channels, Settings); global search (⌘K); notifications bell with unread badge; user menu (profile, sign out).

Backend:
- [ ] `GET /me/unread-counts` → `{ inbox, notifications, teamsUnread }` for badges.
- [ ] `GET /search?q=&limit=` (see cross-cutting).
- [ ] `GET /me`, `PATCH /me` for the user menu.

---

### 3.3 Conversations (Inbox) — [src/pages/Conversations.jsx](src/pages/Conversations.jsx)
**UI**: 3-pane layout.
- Left: channel dropdown (All / WhatsApp / Instagram / Live Chat / Mails); search; tabs `All | Pending 263 | Unassigned 49`; filter menu (assignment, unread-only, has-attachments); conversation list with avatar + channel badge, preview, assignee chip, unread counter.
- Center: chat header with Call button, channel switcher (same customer across WhatsApp / Mail / Call), chat bubbles with delivery ticks, reply-to quoting, system event lines (e.g. "Product Issue Report task started"), paste/drop attachments, "+" menu to attach Photo / File / Link / Ticket, AI Draft chip, send (Enter / Shift-Enter).
- Right rail: Customer, Tickets, Recordings, Timeline, Attachments, Notes panels — each slides in/out.

Data shape the page expects (see the `threads` array and the selected-thread object):
```
Conversation {
  id, customerId, channel, date, preview,
  phone?, email?, isPhone,
  assignee?, unread,
  orderId?, customer?, notes[], recordings[], attachments[], tickets[],
  messages: [{ from: 'me'|'them'|'system', at, text, delivered?, fromName?, to?, attachments?, replyTo? }]
}
```

Backend:
- [ ] `GET /conversations?channel=&tab=&q=&filters=&cursor=` → paginated list (the tab counters `Pending 263 / Unassigned 49` need live values).
- [ ] `GET /conversations/:id` with `messages`, `customer`, `tickets[]`, `attachments[]`, `notes[]`, `recordings[]`.
- [ ] `POST /conversations/:id/messages` → body `{ text, attachments[], replyTo?, channel }`. Broadcasts over WS so the receiving pane live-updates.
- [ ] `POST /uploads` → presigned URL for images/files (Photo / File menu).
- [ ] `POST /conversations/:id/notes` (internal only).
- [ ] `POST /conversations/:id/typing` (optional; the UI doesn't show typing yet but the Teams page expects parallel behavior).
- [ ] `GET /customers/:id/recordings` — the Recordings side panel.
- [ ] `POST /messages/:id/read` — mark read / unread.
- [ ] **Inbound connectors** (separate services): WhatsApp Business Cloud API webhook, Gmail/Outlook IMAP or Gmail Push, Instagram Graph webhook, web-widget WebSocket. Each normalizes into `messages` + fans out to the WS.
- [ ] **AI reply pipeline**: on every inbound message run the configured agent (see Agent Builder). If confidence high → auto-send via the same channel connector. If not → create/attach a ticket and surface the "AI Draft" chip so an agent can 1-click send.
- [ ] `GET /channels/for-customer/:id` so the header's channel switcher knows which identities a customer has.

---

### 3.4 Tickets — [src/pages/Tickets.jsx](src/pages/Tickets.jsx)
**UI**: 3-pane.
- Left: filter dropdown (`All Open`, `My Tickets`, `Unassigned`, `Blocked`, `My Created · In Progress`, `My Created`), sort menu (Newest / Oldest / SLA asc/desc / Customer A–Z), search, ticket cards (channel avatar, `#id`, customer, task tag, status pill `Assigned/Queued/Blocked/Resolved/Closed`, SLA chip, assignee avatar).
- Center: detail tabs `Info | Conversation | Notes (1) | Customer`. Info tab renders Customer card, Activity timeline, Order card (line items, price, SKU).
- Right sidebar (collapsible): Properties (Status, Source, Task, Team, Order placed), Assignment, **Pending child ticket** (assigneeEmail, team, prompt, resolution dropdown), Resolution/Execution tabs, VOC select, Save.
- "Expand" button opens a full-screen modal with Customer / Order / Overview / Notes / Resolution / Timeline / Custom fields.
- Top bar "New Ticket" opens [src/components/CreateTicketModal.jsx](src/components/CreateTicketModal.jsx).

Ticket shape (see `tickets` seed array):
```
Ticket {
  id, ago, customer, channel, task, assignee?, status, sla, source, team,
  orderStatus?, orderPlacedOn?,
  customerDetails: { since, phone, email, address, orders, returns },
  timeline: [{ at, text, actor? }],
  order: { id, price, placedOn, items: [{ name, sku, qty, price }] },
  notes: [{ author, at, text }],
  customFields: { [k]: string|bool|array },
  pendingChild?: { assigneeEmail, team, objective, prompt, resolution }
}
```

Backend:
- [ ] `GET /tickets?filter=&sort=&q=&cursor=` — with counts for each `FILTERS` key.
- [ ] `GET /tickets/:id` with nested `customer`, `order`, `timeline`, `notes`, `customFields`, `pendingChild`.
- [ ] `POST /tickets` (new ticket — see modal spec below).
- [ ] `PATCH /tickets/:id` — partial update for status, assignee, team, task, source, VOC, resolution, customFields.
- [ ] `POST /tickets/:id/notes` (internal).
- [ ] `POST /tickets/:id/child` and `PATCH /tickets/:id/child` — create/resolve a child ticket that is blocking the parent.
- [ ] `POST /tickets/:id/resolve` with `{ voc, resolutionNotes, disposition }` — the "Save" button in the Resolution tab.
- [ ] **SLA engine**: compute `sla` remaining per ticket; emit `sla.breach_warning` at T-30min (the Notifications feed already models this). Policies should be configurable per team/task.
- [ ] **Task taxonomy**: `task` is a controlled vocab (`delay_delivery`, `refund_pending`, `not_delivered_agent_handoff`, `product_issue_report`, `address_change`, `product_quality`, …). Admin UI for these is not yet built but endpoints should support it.

**Create Ticket modal** — [src/components/CreateTicketModal.jsx](src/components/CreateTicketModal.jsx):
- Customer finder: search by phone / email / Shopify ID / name. "Create as new customer" path for guests.
  - [ ] `GET /customers/search?q=&type=phone|email|shopify|name`
  - [ ] `POST /customers` (guest-create)
- Similar-tickets detection (embedding search, client currently fakes it): shows % match, "same customer" badge, Open / Merge actions.
  - [ ] `POST /tickets/similar` with `{ subject, description, customerId? }` → ranked list `{ ticket, score }[]`. Needs an embedding index over subject/description/keywords.
  - [ ] `POST /tickets/:id/merge` (for the "Merge" button).
- Order picker (link optional order to ticket); channel picker (WhatsApp/Chat Widget/Instagram/Email/Voice Call — disabled if no phone); priority; tags; "share initial response with customer" toggle.
  - [ ] `POST /tickets` body: `{ customerId, subject, description, channel, priority, orderId?, tags[], shareWithCustomer }`.

---

### 3.5 Customers — [src/pages/Customers.jsx](src/pages/Customers.jsx)
**UI**: list + detail. Filters (`All / With orders / With open tickets / With returns`), sort (Recent / Name / Orders / Tickets). Customer card shows avatar, phone, email, order count, return count, open-ticket count.
Detail: profile (phone/email/address + orders/returns stats + other addresses), Order details collapsible (id, placed-at, status, items, price), Support tickets list (each row links back to `/tickets?id=...`).

Backend:
- [ ] `GET /customers?filter=&sort=&q=&cursor=` with per-filter counts.
- [ ] `GET /customers/:id` — full profile with `recentOrders`, `tickets`, `otherAddresses`.
- [ ] `GET /customers/lookup?phone=&email=&shopifyId=&name=` (used by modal finder and deep-link from Tickets modal / Global search).
- [ ] Orders come from the Shopify connector (see Channels → E-Commerce). They must be linked to the customer on ingestion.
- [ ] Ticket summaries on a customer must be denormalized or joinable for this page.

---

### 3.6 Teams (internal chat) — [src/pages/Teams.jsx](src/pages/Teams.jsx), [src/data/teamsData.js](src/data/teamsData.js)
**UI**: Slack-like. Left pane = Channels + DMs with unread badges, collapsible. Center = chat with mentions (`@`), reactions (10 emoji picker), reply threading, typing indicator, attach menu (Share ticket / Share customer / Image / File). Right = channel/DM info.
Members have `status: online|away|offline` and `role`.

Backend:
- [ ] `GET /teams/members` — with presence.
- [ ] `GET /teams/channels`, `POST /teams/channels` (Create channel — stubbed as `alert()` today).
- [ ] `GET /teams/dms`, `POST /teams/dms` (Start DM — stubbed).
- [ ] `GET /teams/conversations/:id/messages?cursor=` — `messages[]` with `blocks[]` (text | mention | channel-mention), `reactions[]`, `replyTo`, `attachments[]`.
- [ ] `POST /teams/conversations/:id/messages` → ingest a message with parsed blocks (see `parseDraftToBlocks`).
- [ ] `POST /teams/messages/:id/reactions` (toggle).
- [ ] `POST /teams/typing/:id` / WS event.
- [ ] Presence: `GET /presence`, WS `presence.changed`.
- [ ] Attach-by-ticket / attach-by-customer — reuses ticket/customer endpoints.

---

### 3.7 Notifications — [src/pages/Notifications.jsx](src/pages/Notifications.jsx), [src/hooks/useNotifications.js](src/hooks/useNotifications.js), also surfaced in TopBar.
**UI**: typed feed (`assign | reply | sla | broadcast | member`), filters, group-by-day (Today / Yesterday / This week / Earlier), mark-read, dismiss, mark-all-read, clear-all. Each notification has `link` that deep-navigates.

Backend:
- [ ] `GET /notifications?cursor=` — with `unread` count.
- [ ] `POST /notifications/:id/read`, `POST /notifications/read-all`, `DELETE /notifications/:id`, `DELETE /notifications` (clear-all).
- [ ] WS `notification.created` push.
- [ ] **Generators** — background jobs should create notifications on: ticket assign / reassign, customer reply, SLA breach warning, broadcast sent, teammate joined. (These types map 1:1 to `TYPE_META` in the frontend.)

---

### 3.8 Platforms — [src/pages/Platforms.jsx](src/pages/Platforms.jsx)
**UI**: card per channel (WhatsApp, Email, Instagram, Web widget, Slack, Telegram, Messenger, Discord) with status (connected / pending / disconnected), tickets count, Connect/Manage toggle. Header stats: Connected count, Tickets today, Pending setup, Avg response.

Backend:
- [ ] `GET /platforms` — each with `status`, `tickets` (per-day count), config id.
- [ ] `POST /platforms/:id/connect` — kicks off the channel-specific OAuth / webhook registration.
- [ ] `POST /platforms/:id/disconnect`.
- [ ] `GET /platforms/stats` — Connected, Tickets today, Pending setup, Avg response time.
- [ ] Actual connectors:
  - WhatsApp Business Cloud API (phone ID, verify token, webhook).
  - Gmail / Outlook via OAuth + push notifications / IMAP idle.
  - Instagram Graph API (Page + IG Business).
  - Web widget: serve an embeddable script + WS endpoint.
  - Slack app install (bot token, events subscription).

---

### 3.9 Channels (advanced) — [src/pages/channels/](src/pages/channels/)

All five subpages share a sidenav layout ([ChannelsLayout](src/pages/channels/ChannelsLayout.jsx)).

**3.9.1 Overview** — [ChannelsOverview.jsx](src/pages/channels/ChannelsOverview.jsx) lists Messaging, E-commerce platforms, Shipping partners with connect/disconnect and a "Request integration" CTA.
- [ ] Same shape as `/platforms` but wider catalog.

**3.9.2 E-Commerce** — [ECommerce.jsx](src/pages/channels/ECommerce.jsx) shows connected stores (Shopify / Unicommerce) with `lastSync`, `orders30d`, `health`. KPIs: Total orders 30d, Active stores, Avg sync latency, Failed webhooks. "Recent activity" feed (orders, syncs, webhook retries, refunds).
- [ ] `GET /ecommerce/stores`, `POST /ecommerce/stores`, `DELETE /ecommerce/stores/:id`, `POST /ecommerce/stores/:id/sync`.
- [ ] `GET /ecommerce/events?cursor=` for the activity feed.
- [ ] Shopify / Unicommerce adapters with webhook handlers for `orders/*`, `products/*`, `refunds/*`.

**3.9.3 API Library** — [ApiLibrary.jsx](src/pages/channels/ApiLibrary.jsx). Two tabs: **APIs** (method, name, integration, calls24h, p95, last used, Edit/Clone/Test/Delete) and **Integrations** (name, api count, description, health, Show APIs / Delete).
- [ ] `GET /apis`, `POST /apis`, `PATCH /apis/:id`, `POST /apis/:id/clone`, `DELETE /apis/:id`, `POST /apis/:id/test`.
- [ ] `GET /integrations`, `POST /integrations`, `DELETE /integrations/:id`.
- [ ] These are the **tool catalog** the AI agent calls at runtime — they are the executable bridge between the agent and external systems.

**3.9.4 API Logs** — [ApiLogs.jsx](src/pages/channels/ApiLogs.jsx). Per-call log with time, API name, execute action, customer id, HTTP status, latency; filters by status class (2xx/4xx/5xx) and API; detail panel shows request/response payload.
- [ ] `GET /api-logs?status=&apiId=&q=&from=&to=&cursor=`
- [ ] `GET /api-logs/:id` — full request/response, headers (secrets redacted).
- [ ] `GET /api-logs/stats` — 24h totals per status class, success rate, deltas.

**3.9.5 Google Sheets** — [GoogleSheets.jsx](src/pages/channels/GoogleSheets.jsx). Validate sheet URL, list connected sheets (tabs, rows, last sync), pause/resume/remove; shows service-account email to share the sheet with.
- [ ] `GET /sheets`, `POST /sheets/validate` (resolve URL + tabs + row count), `POST /sheets`, `PATCH /sheets/:id` (pause/resume), `DELETE /sheets/:id`.
- [ ] Sheet-sync worker + a provisioned GCP service account.

**3.9.6 Health** — [Health.jsx](src/pages/channels/Health.jsx). Per-service status (operational / degraded / outage), 30-day uptime bars, p95, error rate; incidents feed with status (investigating / identified / monitoring / resolved).
- [ ] `GET /health/services` — each with `{ uptime30d, latencyP95, errorRate, lastCheck, status }`.
- [ ] `GET /health/services/:id/uptime?window=24h|7d|30d|90d` — series for the bar graph.
- [ ] `GET /incidents?cursor=`, `POST /incidents/subscribe` (email alerts).
- [ ] Background prober that pings each integration every N seconds.

---

### 3.10 Analytics — [src/pages/Analytics.jsx](src/pages/Analytics.jsx)
**UI**: today: Tickets this week, Avg resolution, CSAT, AI deflection %. Chart area is a placeholder.

Backend:
- [ ] `GET /analytics/summary?range=7d|30d|…` → 4 KPI cards.
- [ ] `GET /analytics/timeseries?metric=tickets|resolution|csat|deflection&range=` for the future charts.
- [ ] CSAT survey mechanism (post-resolve email/WA survey) — not wired on the UI yet but needed to feed this number.

---

### 3.11 Agent Builder — [src/pages/AgentBuilder.jsx](src/pages/AgentBuilder.jsx)

**UI contract**: one **"Create Agent"** button on this page. Click → a new tab opens the **self-hosted Langflow** (already authenticated as the current user — no extra login). The user designs their flow there and hits **Publish**. On publish, Langflow pings our backend; the agent appears in the list on `/agent-builder` and is immediately usable by the AI reply pipeline. That's the whole user-facing surface.

The existing form on this page (name, model, persona, temperature, tool toggles) stays as a **read-only summary** for each published agent — showing the metadata, `active_version`, last-published time, and a *Run test* button. All editing happens inside Langflow.

#### Frontend changes

1. Replace the "Save agent / Test agent" buttons at the top with one primary button:
   ```jsx
   <a
     className="btn"
     href="/flow/launch"           // server endpoint — NOT Langflow's URL directly
     target="_blank"
     rel="noopener"
   >
     <Icon.Plus /> Create Agent
   </a>
   ```
2. Below it render a grid of agent cards from `GET /agents`: name, model badge, status (`draft | published`), version, *Edit in Langflow* (opens `/flow/launch?agent=<id>`), *Test*, *Archive*.
3. On mount, open a WebSocket subscription to `agents:<workspace_id>` so a newly-published agent pops in without a reload.

No iframe. No mode toggle. One button.

#### Backend endpoints

- [ ] `GET /agents`, `GET /agents/:id`, `PATCH /agents/:id` (archive/rename only), `DELETE /agents/:id`.
  - Shape: `{ id, name, model, status, active_version, last_published_at, input_schema, output_schema, stats: { runs_24h, p95_ms, cost_30d } }`.
- [ ] `GET /flow/launch?agent=<id?>` — SSO redirect into Langflow. **Primary user-facing endpoint** (see §A).
- [ ] `POST /webhooks/langflow/published` — Langflow → us, fires when a user publishes. Creates or updates the `agents` row (see §B).
- [ ] `POST /agents/:id/test` (SSE) — sandbox invocation.
- [ ] `POST /agents/:id/run`  (SSE) — production invocation, used by the AI reply pipeline.
- [ ] `GET  /agents/:id/runs?cursor=` — run history with per-node trace.

#### A. Self-hosted Langflow + SSO (the "click → open Langflow as me" flow)

**Deploy** — run Langflow privately inside your VPC, reachable only via your reverse proxy:
```yaml
# docker-compose.yaml (extract)
langflow:
  image: langflowai/langflow:latest
  environment:
    LANGFLOW_DATABASE_URL: postgresql://langflow:***@postgres:5432/langflow
    LANGFLOW_AUTO_LOGIN: "false"
    LANGFLOW_SUPERUSER:          admin@workspacex.com
    LANGFLOW_SUPERUSER_PASSWORD_FILE: /run/secrets/langflow_superuser_pw
    LANGFLOW_SECRET_KEY_FILE:         /run/secrets/langflow_secret_key
    LANGFLOW_NEW_USER_IS_ACTIVE: "true"
  ports: ["7860:7860"]
```
Expose on an internal hostname, e.g. `https://flow.workspacex.com`. Put your reverse proxy (Nginx / Caddy / Traefik) in front so the browser only talks to `flow.workspacex.com` via your domain.

**Provisioning** — the first time any user in a workspace clicks *Create Agent*:

1. WorkspaceX API calls `POST https://flow.workspacex.com/api/v1/login` with the superuser credentials (from Vault) → long-lived admin token, cached in Redis as `langflow:admin_token`.
2. Ensure the **workspace has a Langflow folder** (= tenant boundary inside Langflow):
   ```
   POST /api/v1/folders { name: "<workspace_slug>" }
   ```
   Persist `workspaces.langflow_folder_id`. Created once, reused forever.
3. Ensure the **user has a Langflow user**:
   ```
   POST /api/v1/users { username: "<workspace_slug>:<user_id>", password: <random>, is_active: true }
   PATCH /api/v1/folders/<folder_id>/users  (add this user)
   ```
   Persist `users.langflow_user_id`. Random password is stored in Vault — the user never sees it.
4. Generate a **fresh Langflow session**:
   ```
   POST /api/v1/login  { username, password }
   → { access_token (JWT, 7-day), refresh_token }
   ```

**The `GET /flow/launch` redirect** — what the *Create Agent* button hits:

```python
def flow_launch(request):
    user = require_session(request)                       # WorkspaceX session cookie
    ensure_langflow_user(user)                            # idempotent, uses steps 2-3
    access_token, refresh_token = langflow_login(user)    # step 4 — every click

    # Optional: pre-create a draft flow so the user lands in the editor, not the list.
    flow_id = request.query.get('agent')
    if flow_id:
        target = f"/flow/flow/{flow_id}"
    else:
        draft = langflow_api.post(f"/flows", json={
          "name": f"Draft from {user.name}",
          "folder_id": user.workspace.langflow_folder_id,
          "data": DEFAULT_TEMPLATE,                       # blank graph with our custom Reply node
        })
        target = f"/flow/flow/{draft['id']}"

    # Write Langflow's session cookie on OUR domain so the next /flow/** request is authenticated.
    response = redirect(target)
    response.set_cookie(
        "access_token_lf", access_token,
        domain=".workspacex.com", secure=True, httponly=True, samesite="Lax",
        max_age=7*24*3600,
    )
    response.set_cookie("refresh_token_lf", refresh_token, ...)
    return response
```

**The `/flow/**` reverse proxy** — serves Langflow under our own domain:
- Nginx/Caddy rule: `location /flow/ { proxy_pass https://flow-internal; proxy_set_header Cookie "access_token_lf=$cookie_access_token_lf;"; }`.
- Strip `Set-Cookie` rewrites so they always land on `.workspacex.com`.
- Pass through `Upgrade: websocket` (Langflow uses WS for node-by-node streaming).
- Enforce path guard: before proxying any `/flow/flow/<id>`, check the user has permission to that flow id (`flow.folder_id == user.workspace.langflow_folder_id`). Otherwise 403.

**Net effect for the user**: click *Create Agent* → new tab opens → they're already logged in as themselves → they see only their workspace's flows → they can build and publish.

#### B. Publish hook — Langflow → WorkspaceX

Langflow triggers a webhook on publish. Two equally good ways to wire it:

**Option 1 — Langflow's built-in webhook node.** Ship a template flow that includes a terminal "WorkspaceX Publish" webhook node pointing to our internal endpoint. The user clicks *Publish* inside Langflow; the node fires.

**Option 2 — Poll Langflow.** Run a 30s worker that lists flows in each folder (`GET /api/v1/flows?folder_id=...`) and diffs `updated_at`. When a flow is newly marked `published=true`, process it. Simpler; slightly less instant.

Go with Option 1 for immediacy; fall back to Option 2 as a safety net.

**The webhook**:
```
POST /webhooks/langflow/published
X-Langflow-Signature: sha256=<hmac>
Content-Type: application/json

{ "flow_id":      "...",
  "folder_id":    "...",
  "published_by": "workspace_slug:user_id",
  "name":         "Refund triage",
  "version":      7 }
```

Handler:

1. Verify HMAC against `LANGFLOW_WEBHOOK_SECRET`.
2. Resolve workspace from `folder_id`, user from `published_by`.
3. Pull the canonical flow JSON:
   ```
   GET /api/v1/flows/<flow_id>/export
   ```
4. Derive `input_schema` + `output_schema` from the flow's `ChatInput` / `ChatOutput` nodes (typed inputs map straight to JSON Schema).
5. Upsert:
   ```
   INSERT INTO agents (workspace_id, name, kind, langflow_flow_id, langflow_folder_id,
                       status, active_version, input_schema, output_schema, last_published_at, created_by)
   VALUES (..., 'langflow', <flow_id>, <folder_id>,
           'published', <version>, <schema>, <schema>, now(), <user_id>)
   ON CONFLICT (workspace_id, langflow_flow_id) DO UPDATE
     SET status='published', active_version=EXCLUDED.active_version,
         input_schema=EXCLUDED.input_schema, output_schema=EXCLUDED.output_schema,
         last_published_at=now();
   ```
6. Snapshot flow JSON into `agent_flow_snapshots(agent_id, version, flow_json)`.
7. Fire `agent.published` over the WebSocket → the Agent Builder page animates the new card into view without a refresh.
8. If this flow was previously registered as a tool for the main support agent, refresh its tool schema.

#### C. Schema additions

```sql
ALTER TABLE workspaces
  ADD COLUMN langflow_folder_id uuid;

ALTER TABLE users
  ADD COLUMN langflow_user_id uuid,
  ADD COLUMN langflow_password_secret_id text;   -- pointer into Vault

ALTER TABLE agents
  ADD COLUMN kind text NOT NULL DEFAULT 'langflow',    -- every user-created agent is langflow now
  ADD COLUMN status text NOT NULL DEFAULT 'draft',     -- 'draft'|'published'|'archived'
  ADD COLUMN langflow_flow_id uuid,
  ADD COLUMN langflow_folder_id uuid,
  ADD COLUMN active_version int NOT NULL DEFAULT 1,
  ADD COLUMN input_schema jsonb,
  ADD COLUMN output_schema jsonb,
  ADD COLUMN last_published_at timestamptz,
  ADD COLUMN created_by uuid REFERENCES users(id),
  ADD CONSTRAINT agents_ws_flow_unique UNIQUE (workspace_id, langflow_flow_id);

CREATE TABLE agent_flow_snapshots (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  version     int  NOT NULL,
  flow_json   jsonb NOT NULL,
  created_by  uuid NOT NULL REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agent_id, version)
);

CREATE TABLE agent_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id),
  agent_id        uuid NOT NULL REFERENCES agents(id),
  conversation_id uuid REFERENCES conversations(id),
  version         int NOT NULL,
  input           jsonb NOT NULL,
  output          jsonb,
  status          text NOT NULL,          -- 'ok'|'error'|'timeout'
  latency_ms      int,
  tokens_in       int,
  tokens_out      int,
  cost_usd        numeric(10,4),
  trace           jsonb,                  -- node-by-node
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON agent_runs (agent_id, at DESC);
```

#### D. Running a published agent

The AI reply pipeline in §10 calls:
```
POST https://flow.workspacex.com/api/v1/run/<langflow_flow_id>?stream=true
Authorization: Bearer <service_token_for_workspace>
{
  "input_value": "<customer message>",
  "session_id":  "<conversation_id>",
  "tweaks":      { "WorkspaceContext": { "workspace_id": "...", "customer_id": "..." } }
}
```

- `session_id = conversation_id` gives the flow stateful memory per customer conversation automatically (Langflow's built-in session store).
- Service token is a Langflow API key bound to a system user with read-only access to the specific folder.
- Stream SSE → same WS channel the frontend already uses for `message.created`, so the bubble types out live.
- Every tool call inside the flow hits `POST /internal/apis/:name` (our WorkspaceX tool node) → logged to `api_logs` → visible in Channels > API Logs.
- Record run in `agent_runs` with `trace`, tokens, cost.

#### E. Flow-as-a-tool (bonus)

A published Langflow flow can itself be enabled as a tool for another agent:
1. In API Library, pick *Add API → From Langflow agent* → select the published agent.
2. We create an `apis` row with `spec = { kind: 'langflow_flow', agent_id, input_schema, output_schema }`.
3. When any other agent enables this tool, its `input_schema` becomes the Anthropic tool schema. The executor calls the flow when the tool is invoked.

Non-engineers can now visually assemble a "detect-fraud-and-refund" sub-agent, publish it, and have the main support agent call it during conversations — no code.

#### F. Secrets, versioning, cost

- **Secrets**: seed Langflow's variable store from Vault at boot (`POST /api/v1/variables` with `$ANTHROPIC_API_KEY`, etc.). Flow JSON exports never carry them.
- **Versioning**: every publish writes a new `agent_flow_snapshots` row; `agents.active_version` points at the live one. A future *Rollback* button just flips the pointer.
- **Cost/rate caps**: per-workspace daily ceiling enforced in the runner; when exceeded, auto-escalate to a human via the existing `escalate` tool instead of erroring the customer.
- **Tenant isolation**: every Langflow API call is sanity-checked — `flow.folder_id == session.workspace.langflow_folder_id`. Same check on the reverse proxy for `/flow/**` requests.
- **Deleted flows**: if a user deletes a flow inside Langflow, mark the matching `agents` row `archived`; never hard-delete so historical `agent_runs.trace` stays readable.

#### G. Gotchas

- **Login drift**: the Langflow access token is 7 days; our session cookie is 30. Refresh Langflow's token silently on each `/flow/launch` hit — users never see Langflow's login screen.
- **WebSocket through proxy**: Langflow streams node updates via WS. Nginx needs `proxy_http_version 1.1` + `Upgrade`/`Connection` headers on `/flow/`.
- **CSP**: `/flow/**` needs laxer `script-src`/`style-src`. Scope the loosening to that path only; keep the rest of the app strict.
- **Slow flows**: enforce a 60s wall-time for inline runs; longer flows go via the queue and notify the conversation when ready.
- **Input-schema drift**: if the user removes the `ChatInput` node that defines the tool schema, mark dependent agents `needs_review` and pause the tool registration until reconciled.

#### H. Built-in orchestrator agent (default, always-on)

Every workspace ships with a hard-coded default agent named **`default-orchestrator`**. If the admin hasn't published anything in Langflow yet, the AI reply pipeline falls back to this agent — so the product is useful from day one, without any agent-authoring work.

**Framework**: [**Pydantic AI**](https://ai.pydantic.dev) — a typed, minimal agent framework where every tool is a Python function with Pydantic-validated args + returns. Fits naturally with FastAPI, and cheap to run alongside the API (no separate Langflow hop).

**Pattern**: an **orchestrator** (router) with every safe WorkspaceX capability registered as a typed tool. The model decides which tools to call and in what order.

##### Architecture

```
┌────────────────────────┐  message   ┌──────────────────────────┐
│ AI reply pipeline      ├───────────►│  pick agent              │
│ (§10)                  │            │  - published Langflow?   │
└────────────────────────┘            │  - else → default        │
                                      └──────┬───────────────────┘
                                             │
                                             ▼
                                   ┌──────────────────────┐
                                   │ default-orchestrator │
                                   │ (Pydantic AI)        │
                                   │                      │
                                   │  @agent.tool ...     │
                                   │  @agent.tool ...     │
                                   └──────┬───────────────┘
                                          │ typed tool calls
                                          ▼
                         customers · orders · tickets · kb · channels · apis
```

##### Selection logic (inside the reply pipeline)

```python
active = await db.fetchrow("""
  SELECT * FROM agents
  WHERE workspace_id = $1
    AND status = 'published'
    AND is_active = true
  ORDER BY last_published_at DESC
  LIMIT 1
""", ws_id)

if active:
    stream = run_langflow_agent(active, ctx, message)   # §3.11.D
else:
    stream = run_default_orchestrator(ctx, message)     # this section
```

Optional override: a workspace-level setting `ai.force_default=true` always uses the built-in, even when Langflow agents exist — handy for A/B and incident fallback.

##### Wiring with Pydantic AI

```python
# app/ai/orchestrator.py
from dataclasses import dataclass
from uuid import UUID
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

@dataclass
class AgentCtx:
    workspace_id: UUID
    conversation_id: UUID | None
    customer_id: UUID | None
    channel: str | None
    actor_user_id: UUID | None          # None when AI acts autonomously
    trace_id: str

orchestrator = Agent(
    model='anthropic:claude-opus-4-7',          # overridable per workspace
    deps_type=AgentCtx,
    retries=2,
    system_prompt=DEFAULT_SYSTEM_PROMPT,
)
```

Every tool is a typed function — Pydantic validates args coming back from the model and serializes returns:

```python
class FindCustomerArgs(BaseModel):
    phone: str | None = None
    email: str | None = None
    shopify_id: str | None = None
    name: str | None = None

class CustomerOut(BaseModel):
    id: UUID; name: str; phone: str | None; email: str | None
    orders: int; returns: int; since: str | None

@orchestrator.tool
async def find_customer(ctx: RunContext[AgentCtx], args: FindCustomerArgs) -> CustomerOut | None:
    """Look up an existing customer by phone, email, Shopify id, or fuzzy name."""
    return await customer_repo.search(ctx.deps.workspace_id, **args.model_dump(exclude_none=True))
```

Tool failures surface as typed errors; the model can self-correct or escalate.

##### Tools to register (the MVP orchestrator surface)

All of these already exist as backend services — the tools are thin wrappers that add Pydantic typing + audit logging.

| Tool | Purpose | Notes |
|---|---|---|
| `find_customer`          | search by phone/email/shopify/name | no-op if nothing found → model should ask or create guest |
| `get_customer(id)`       | full profile incl. addresses       | |
| `get_order(id)`          | order detail                       | |
| `list_customer_orders`   | recent orders for a customer       | `limit` enforced server-side |
| `get_shipping_status`    | courier + ETA                      | calls Clickpost/Shiprocket API from the library |
| `kb_search(query, k=5)`  | vector search over `kb_chunks`     | reuses similar-ticket infra |
| `similar_tickets`        | find dupes for a subject           | pgvector HNSW |
| `create_ticket`          | new ticket from current convo      | auto-linked to `conversation_id` from ctx |
| `update_ticket`          | status/assignee/team/VOC           | triggers timeline entry |
| `create_child_ticket`    | block parent on a sub-task         | matches the UI's "Pending Child Ticket" flow |
| `add_ticket_note`        | internal note                      | |
| `issue_refund`           | initiate refund                    | **hard-capped ₹10,000** server-side; above → escalate |
| `send_reply`             | send message on the convo's channel| routes via the right adapter (WA/Gmail/etc) |
| `escalate_to_human`      | assign + reply "I've looped in…"   | takes `reason`, `team`; writes timeline |
| `run_library_api(name, args)` | catch-all: calls any API from the API Library by name | gated by a per-workspace allowlist |

Adding a new API in the **API Library** (§3.9.3) auto-registers it as a callable under `run_library_api` — no code change required. Admins can enable/disable each one per agent in settings.

##### System prompt (hard-coded baseline)

```
You are WorkspaceX's AI support agent for {workspace_name}.

When a customer writes in:
  1. Identify them via `find_customer` using phone/email/handle from the
     conversation context. If unknown, ask for an order id or email.
  2. Consult `kb_search` and `list_customer_orders` before answering.
  3. If confident, reply using `send_reply`. Never reply without calling
     `send_reply` — text you emit otherwise is invisible to the customer.
  4. If uncertain, unsure, or the request requires a refund above the cap,
     call `escalate_to_human` with a short `reason` and the right `team`.
  5. Always call `create_ticket` or `update_ticket` so the case is tracked.
  6. Match the customer's language. Be concise, warm, specific.
  7. Never invent order numbers, prices, or policies. Never disclose PII
     beyond what the customer has already shared in this conversation.
```

Workspaces can append to this via `workspace.ai_prompt_append` without replacing the base — keeps guardrails intact.

##### Running it

```python
async def run_default_orchestrator(ctx: AgentCtx, user_message: str):
    async with orchestrator.run_stream(user_message, deps=ctx) as stream:
        async for event in stream:
            # event: ModelTextDelta | ToolCall | ToolResult | FinalResult
            yield to_sse(event)
```

- Streams SSE → the WebSocket fan-out pushes chunks to `conv:<id>` subscribers, same path Langflow uses. Typing animation is identical.
- On completion, write one `agent_runs` row (§3.11.C) with `version=0` (0 = built-in), tokens, cost, and the full tool-call trace.
- Each tool call also writes to `api_logs` so it shows up in Channels > API Logs alongside everything else.

##### Guardrails (enforced in code, not just prompt)

- **Rate limit**: max 20 reply-generations per customer per hour. On overflow, silently queue + escalate.
- **Refund cap**: `issue_refund` rejects amounts > ₹10,000 regardless of what the model asks for. Hard-coded constant in the tool, not a config value.
- **PII boundary**: log outputs are scrubbed (phone/email/addresses → tokens) before hitting `agent_runs.trace` retention after 90 days.
- **Loop guard**: >8 tool calls in a single run → force end-of-turn and escalate. Catches runaway reasoning.
- **Consecutive error**: 3 tool errors in a row → escalate, don't retry further.
- **Tool allowlist**: `workspace.ai_allowed_tools` (jsonb) can disable any tool name per tenant (e.g. disable `issue_refund` for workspaces that don't want AI touching money).

##### Schema additions

```sql
ALTER TABLE workspaces
  ADD COLUMN ai_prompt_append text,
  ADD COLUMN ai_allowed_tools jsonb NOT NULL DEFAULT '[]',   -- empty = all allowed
  ADD COLUMN ai_force_default boolean NOT NULL DEFAULT false;
```

`agent_runs` already covers per-run traces — the default orchestrator writes with `version = 0` and `agent_id = NULL` (or a sentinel UUID) so its stats surface in the Agent Builder page alongside user-published agents.

##### Deploy shape

- Same Python process as the FastAPI app — `pydantic-ai` is lightweight, no separate service needed.
- For Go/Node backends: run the orchestrator as a thin Python sidecar on the same host, exposed over gRPC or HTTP with an internal-only port. One container, minimal latency.
- `pip install pydantic-ai[anthropic]` plus `ANTHROPIC_API_KEY` from Vault; that's the whole install footprint.

##### Why a hard-coded default matters

- **Build 1 can demo the AI loop without Langflow running at all** — easiest path to end-to-end.
- Acts as the **fallback** when a user-published agent errors out or hits its cost cap.
- Gives a **known-good baseline** to diff any custom agent against during regression checks.
- Ships with the same tools any custom agent would use, so admins can learn the capabilities by inspecting the default before authoring their own.

---

### 3.12 Settings — [src/pages/Settings.jsx](src/pages/Settings.jsx)
Six tabs: Profile, Workspace, Preferences (animation level — client-only), Notifications, Security, Billing.

Backend:
- [ ] `GET/PATCH /me` (profile: name, email, role).
- [ ] `GET/PATCH /workspace` (name, timezone, default language).
- [ ] `GET/PATCH /me/notification-prefs` (email alerts, browser push, digest).
- [ ] `POST /me/password`, `POST /me/2fa/enable`, `POST /me/2fa/verify`.
- [ ] `GET /billing/plan`, `GET /billing/usage` (tickets this month, active agents, next invoice), `POST /billing/checkout` (Upgrade), `GET /billing/invoices`.

---

## 4. Data model — rough outline

Suggested core tables. Adjust per your stack.

- `workspaces(id, name, timezone, default_lang, plan)`
- `users(id, workspace_id, name, email, password_hash, role, provider, status, created_at)`
- `customers(id, workspace_id, name, phone, email, shopify_id, since, address, other_addresses jsonb, created_at)`
- `orders(id, customer_id, source, placed_at, total, currency, status, items jsonb)`
- `conversations(id, workspace_id, customer_id, channel, last_message_at, preview, assignee_id, unread_count, status)`
- `messages(id, conversation_id, from_role enum('customer','agent','ai','system'), from_user_id?, at, text, delivered_at, read_at, attachments jsonb, reply_to_id?, metadata jsonb)`
- `tickets(id, workspace_id, customer_id, conversation_id?, order_id?, status, source, channel, task, team, assignee_id?, sla_due_at, priority, custom_fields jsonb, voc?, resolution_notes?, parent_ticket_id?, created_at)`
- `ticket_timeline(id, ticket_id, at, actor, text)`
- `ticket_notes(id, ticket_id, author_id, at, text)`
- `teams_channels(id, workspace_id, name, description, privacy)`
- `teams_members(channel_id, user_id)`
- `teams_messages(id, channel_or_dm_id, user_id, at, blocks jsonb, reply_to_id?, attachments jsonb)`
- `teams_reactions(message_id, user_id, emoji)`
- `notifications(id, user_id, type, title, body, link, ts, unread)`
- `platforms(id, workspace_id, kind, status, config jsonb)`
- `ecommerce_stores(id, workspace_id, platform, name, credentials_secret_id, health, last_sync_at)`
- `apis(id, workspace_id, name, method, integration_id, spec jsonb)`
- `integrations(id, workspace_id, name, kind, config jsonb)`
- `api_logs(id, api_id, workspace_id, customer_id?, trace_id, status, latency_ms, request jsonb, response jsonb, at)`
- `sheets(id, workspace_id, url, name, tabs jsonb, rows, status, last_sync_at)`
- `agents(id, workspace_id, name, model, persona, instructions, temperature, tools jsonb)`
- `health_services(id, workspace_id, kind, status, uptime_30d, latency_p95, error_rate, last_check_at)`
- `incidents(id, workspace_id, title, status, components text[], started_at, summary, tone)`

---

## 5. Priority order

### 🎯 Build 1 — the MVP spine (Auth ↔ Conversation ↔ Ticket, end-to-end)

**This is the primary goal. Nothing else ships before this works end-to-end.** The demo we want at the end of Build 1:

> A user signs in → a customer message arrives on **one** channel → the system creates or finds the customer, opens/updates a conversation, promotes it into a ticket, notifies the assignee over WebSocket, the agent replies from the conversation pane, the reply goes out on the same channel, and every state change shows up in the ticket timeline.

Everything on this list is mandatory for Build 1; treat it as one connected slice, not parallel tracks.

1. **Auth + session** (§3.1, §7 `users`, `auth_sessions`, `workspaces`)
   - [ ] Password signup/signin with Argon2id + session cookie.
   - [ ] `GET /auth/me` rehydrate + `POST /auth/signout`.
   - [ ] Google OAuth end-to-end (Outlook can wait if time-boxed).
   - [ ] RBAC middleware: admin / agent / viewer resolved on every request.
   - [ ] Workspace scoping enforced on every query (Postgres RLS or app-level `WHERE workspace_id = $ws`).
   - ✅ *Exit check*: a real user can sign up, sign in, sign out, be dropped at `/conversations`, and only see their workspace's data.

2. **Customer model** (§3.5, §7 `customers`)
   - [ ] `customers` table with indexes on `phone`, `email`, `shopify_id`, `wa_id`, `psid`.
   - [ ] `POST /customers` (manual + from a webhook), `GET /customers/search?q=` used by the create-ticket modal and the inbound pipeline.
   - [ ] Guest/visitor kind for anonymous web-widget chats.
   - ✅ *Exit check*: given an inbound WhatsApp `wa_id` or Gmail `From` header, the system finds the existing customer or creates one deterministically — no duplicates.

3. **Conversation linking** (§3.3, §7 `conversations`, `messages`)
   - [ ] `conversations (workspace_id, channel, external_thread_id)` UNIQUE — this is the linking key. Every inbound message resolves `(channel, external_thread_id)` → upsert conversation → append message.
   - [ ] `messages` with `external_message_id` dedupe, `reply_to_id` threading, `from_role` ∈ `customer | agent | ai | system`, `at`, attachments jsonb.
   - [ ] One inbound adapter end-to-end — **pick WhatsApp Cloud API** (§6.3) because setup is cleanest and webhooks are synchronous. Gmail (§6.1) is the fallback if Meta verification stalls.
   - [ ] One outbound send path — the same adapter.
   - [ ] `GET /conversations`, `GET /conversations/:id`, `POST /conversations/:id/messages`.
   - [ ] `POST /uploads` presigned URLs for attachments.
   - [ ] WebSocket topics `conv:<id>` and `inbox:<ws>` pushing `message.created` / `conversation.updated` so the React UI updates live without refetch (see §8 optimistic-UI contract).
   - ✅ *Exit check*: send a WhatsApp message from a real phone → appears in the inbox list + chat pane within 2 s, unread badge moves; agent replies in the UI → customer's phone buzzes.

4. **Ticket system** (§3.4, §7 `tickets`, `ticket_timeline`, `ticket_notes`)
   - [ ] `tickets` table with FKs to `customers` + `conversations`, enum `status`, `task` taxonomy seeded with the values the mock data uses (`delay_delivery`, `refund_pending`, `not_delivered_agent_handoff`, `product_issue_report`, `delivery_followup`, `address_change`, `product_quality`).
   - [ ] **Auto-creation rule**: on first inbound message in a *new* conversation, create a ticket with `status='queued'`, `source=channel`, `channel=channel`, `customer_id`, `conversation_id`. Link the conversation back via `conversations.ticket_id` (or leave the 1→N relation on the ticket side — either works; pick one and document).
   - [ ] `GET /tickets`, `GET /tickets/:id`, `PATCH /tickets/:id` — status / assignee / team / task / VOC / resolution_notes / custom_fields.
   - [ ] `POST /tickets` manual create (the Create Ticket modal flow, minus the similar-tickets embedding — that ships in Build 2).
   - [ ] `POST /tickets/:id/child` for the pending-child-ticket flow the UI renders.
   - [ ] `ticket_timeline` trigger (or service-layer writer) emits one row on every state change; SLA due_at computed on create from a hard-coded policy for now.
   - [ ] WebSocket `ticket.updated` / `ticket.timeline.appended` topics so the detail pane live-refreshes.
   - ✅ *Exit check*: an inbound message creates a ticket automatically; assigning it to a user notifies that user; updating status + saving resolution persists and is visible to another agent in real time.

5. **Inbox ↔ Tickets glue** (wires §3 and §4)
   - [ ] On the conversation detail, the right-rail Tickets panel pulls via `GET /conversations/:id/tickets`.
   - [ ] Agent reply from the chat pane is attributed `from_role='agent'`, `from_user_id=<session>`; timeline entry appended to the linked ticket: *"Agent <name> replied on <channel>"*.
   - [ ] Global search (§cross-cutting) needs to at least return conversations + tickets + customers so the TopBar works; a simple `ILIKE` with the `pg_trgm` indexes in §7 is fine for Build 1.
   - ✅ *Exit check*: from the ticket pane, you can see the live conversation, send a reply, and the ticket's timeline reflects it — one loop, no refetches.

**Non-goals inside Build 1** (defer to Build 2+):
- AI replies — leave the "AI Draft" chip grayed out until Build 2.
- Teams internal chat.
- Notifications feed (the unread badge count is enough for now — full notifications UI is Build 2).
- Analytics, Health, Sheets, API Library, Agent Builder / Langflow.
- Shopify order sync — ticket info shows a placeholder order card until Build 2.
- Second channel (Gmail, Instagram, widget) — ship WhatsApp only.
- Billing, 2FA, forgot-password email delivery.

**Build 1 tech cuts** (to stay fast):
- Single Postgres, no read replica yet — add in Build 3 when load shows up.
- Redis only for the OAuth state store + Langflow admin token cache (Build 2+).
- No Pub/Sub — Meta's webhook hits us directly, we process inline (<200 ms), retry via the `outbox` table for the outbound send.
- One Go/Node service, no workers split — the WebSocket fan-out runs in the same process using Postgres LISTEN/NOTIFY.

---

### Build 2 — AI reply loop + second channel

- Shopify connector (orders on customer + ticket cards).
- Gmail inbound + outbound (§6.1).
- Agent Builder SSO into Langflow (§3.11) and the publish webhook.
- AI reply pipeline (§10) wired to the first inbound channel: on every customer message run the published agent; auto-send high-confidence replies, surface drafts otherwise.
- Full Notifications feed + the TopBar panel (§3.7).
- API Library + API Logs (§3.9.3, §3.9.4) — needed because Langflow tool calls route through them.

### Build 3 — Scale + breadth

- Remaining channels: Instagram DM (§6.4), web widget (§6.5), Outlook (§6.2).
- Teams internal chat (§3.6).
- Analytics charts + CSAT survey (§3.10).
- Health + incidents (§3.9.6).
- Read replica + PgBouncer + Redis cache layer (§9).

### Build 4 — Polish

- Billing / Stripe (§3.12).
- Sheets connector (§3.9.5).
- Slack escalations (§6.6), voice recordings (§6.7).
- 2FA, forgot-password email, SSO unlinking.
- Mobile / responsive, dark-mode parity audits.

---

### One-screen summary of Build 1 data flow

```
┌────────────┐  webhook   ┌──────────────┐  upsert    ┌──────────────┐
│ WhatsApp   ├───────────►│ /webhooks/wa ├───────────►│  customers   │
│ Cloud API  │            │              │            │  (by wa_id)  │
└────────────┘            └──────┬───────┘            └──────┬───────┘
                                 │                           │
                                 ▼                           ▼
                          ┌──────────────┐   first msg  ┌──────────────┐
                          │ conversations├─────────────►│   tickets    │
                          │ (by wa_id)   │              │ status=queued│
                          └──────┬───────┘              └──────┬───────┘
                                 │ NOTIFY                      │ NOTIFY
                                 ▼                             ▼
                          ┌──────────────────────────────────────────┐
                          │     WebSocket fan-out (LISTEN/NOTIFY)    │
                          └──────┬──────────────────┬────────────────┘
                                 ▼                  ▼
                        conv:<id> topic        ticket:<id> topic
                                 │                  │
                                 ▼                  ▼
                          Inbox pane updates   Ticket pane updates
```

If Build 1 hits that diagram with real credentials, you have a shippable product spine. Everything after is additive.

---

## 6. Channel integration playbooks

Every channel ends up in the same normalized form:

```
InboundMessage {
  workspace_id, channel, external_thread_id, external_message_id,
  from: { kind: 'customer'|'agent', name, phone?, email?, external_id },
  to:   { ... },
  at, text, attachments[], raw (jsonb)  // keep the raw for debugging
}
```

Every outbound reply is produced by `POST /conversations/:id/messages` and dispatched to the channel adapter based on `conversations.channel`. Each adapter below spells out **what to register, what to store, how to pull, how to push, and how to recover**.

---

### 6.1 Gmail

**Goal**: ingest new mails, thread them into a `Conversation`, send replies in-thread, download attachments.

**Choose one ingestion model**:
- **Preferred**: Gmail API + `users.watch` → Cloud Pub/Sub push (sub-minute latency, no polling).
- Fallback: IMAP IDLE (when the customer has a non-Google mailbox served via Gmail delegation — rare).

**One-time setup**:
1. GCP project → enable **Gmail API** and **Cloud Pub/Sub API**.
2. Create Pub/Sub topic `gmail-inbound` + a push subscription to `POST https://api.workspacex.com/webhooks/gmail` with an OIDC token. Grant `gmail-api-push@system.gserviceaccount.com` the Publisher role on the topic.
3. OAuth consent screen (Internal for Workspace, External for consumer). Get the app verified for restricted scopes.
4. Scopes: `https://www.googleapis.com/auth/gmail.modify`, `https://www.googleapis.com/auth/gmail.send`, `openid email profile`.

**Connect flow** (`POST /platforms/gmail/connect`):
1. Redirect admin to `https://accounts.google.com/o/oauth2/v2/auth?...&access_type=offline&prompt=consent&scope=...`.
2. Exchange code → store `{ access_token, refresh_token, expiry, email }` in `platform_credentials` (encrypted with KMS or `pgcrypto`).
3. Call `users.watch` with `{ topicName, labelIds: ['INBOX'] }` → store `history_id`.
4. Schedule a cron to re-call `users.watch` every **6 days** (watch expires after 7).

**Ingestion loop** (`POST /webhooks/gmail`):
1. Verify OIDC signature from Pub/Sub.
2. Body → `{ emailAddress, historyId }`.
3. Look up workspace by `emailAddress`. Load saved `history_id`.
4. Call `users.history.list(startHistoryId=saved)` → paginate until done.
5. For each `messageAdded`: `users.messages.get(id, format=metadata+full)` → parse headers (`From`, `To`, `Cc`, `Subject`, `Message-ID`, `In-Reply-To`, `References`, `Date`), body parts (`text/plain`, `text/html`), attachments (`attachmentId` → `users.messages.attachments.get`).
6. **Thread key** = Gmail `threadId`. Upsert into `conversations` with `external_thread_id = threadId`. Upsert `messages` with `external_message_id = Message-ID` (dedupe on this).
7. Attachments → upload to your S3/GCS bucket, persist the CDN URL in `messages.attachments`.
8. Persist new `history_id` atomically.
9. Fan out `message.created` over the WebSocket to connected agents.

**Sending a reply**:
1. Build an RFC 822 message:
   ```
   From: "Support" <support@acme.in>
   To: lionafsay@gmail.com
   Subject: Re: Please i wantt to return the mattress
   In-Reply-To: <original-message-id>
   References: <original-message-id>
   Content-Type: multipart/mixed; boundary=...
   ```
2. Base64url-encode → `users.messages.send({ raw, threadId })` — passing `threadId` keeps it in-thread in Gmail's UI.
3. On success store the returned `id` as `external_message_id`.

**Token refresh**: if `401` from any call, refresh using `refresh_token`, update expiry. If `invalid_grant`, mark the platform `status='reauth_required'` and raise a notification.

**Recovery / gotchas**:
- Gmail sometimes drops `historyId` if too old — on `404` do a `users.messages.list(q="newer_than:2d", labelIds=['INBOX'])` backfill.
- Never trust `From` for identity matching — prefer `Message-ID` / `threadId` for dedupe, and use the address only to find/create the customer.
- `Subject` may start with `Re:`/`Fwd:` — strip before comparing.

---

### 6.2 Outlook / Microsoft 365

**Goal**: same shape as Gmail, using Microsoft Graph.

**One-time setup**:
1. Register app in Azure AD → redirect URI `https://api.workspacex.com/oauth/outlook/callback`.
2. API permissions (delegated): `Mail.ReadWrite`, `Mail.Send`, `offline_access`, `User.Read`. Request admin consent for Workspace-wide.
3. Enable the **Graph change notifications** endpoint — your webhook URL must be HTTPS and respond to `validationToken` on initial `POST`.

**Connect flow**:
1. `https://login.microsoftonline.com/common/oauth2/v2.0/authorize?...&scope=offline_access%20Mail.ReadWrite%20Mail.Send`.
2. Exchange code → `{ access_token, refresh_token (~90d), id_token }`.
3. Create subscription:
   ```
   POST https://graph.microsoft.com/v1.0/subscriptions
   { "changeType": "created,updated",
     "notificationUrl": "https://api.workspacex.com/webhooks/outlook",
     "resource": "me/mailFolders('Inbox')/messages",
     "expirationDateTime": now + 70 hours,
     "clientState": <random secret> }
   ```
4. Store `subscription_id` + `clientState` + delta token (empty to start).

**Ingestion loop** (`POST /webhooks/outlook`):
1. Respond `200` with echoed `validationToken` on first call.
2. On notifications: validate `clientState`.
3. Call delta query: `GET /me/mailFolders/Inbox/messages/delta?$deltatoken=...`. Persist new `@odata.deltaLink` on completion.
4. For each message: already includes headers + body + attachments ids. For attachments > 4 MB use `/messages/{id}/attachments/{aid}/$value`.
5. **Thread key** = `conversationId` from Graph. Upsert into `conversations`.
6. Fan out WS event.

**Renewal**: cron every 48h: `PATCH /subscriptions/{id}` with a new `expirationDateTime` (max 71h for Mail).

**Send reply**: `POST /me/messages/{id}/reply` (Graph composes in-thread) **or** `POST /me/sendMail` with manual `conversationId` + `internetMessageHeaders`. Prefer the former for threading.

**Gotchas**:
- Graph subscriptions die silently if your endpoint returns non-2xx. Always log + alert on renewal failures.
- `conversationId` changes if someone forwards — keep `internetMessageId` as secondary dedupe.
- Tokens expire every 60–90 min; refresh inline; refresh_token expires after 90 days of inactivity.

---

### 6.3 WhatsApp Business Cloud API (Meta)

**Goal**: 2-way WhatsApp with template and session messaging.

**One-time setup**:
1. Meta Business account → WhatsApp Business Platform → create an app. Get `phone_number_id`, `waba_id`, `business_id`.
2. Generate a **system user permanent access token** (does not expire).
3. Configure webhook: URL `https://api.workspacex.com/webhooks/whatsapp`, verify token `$WA_VERIFY`, subscribe to `messages` + `message_status`.
4. Register message templates in Meta Business Manager for outside-24h-window sends (e.g. `order_update`, `shipping_delay_notice`).

**Inbound webhook**:
```
POST /webhooks/whatsapp
{ entry: [{ id: waba_id,
           changes: [{ value: { messaging_product:'whatsapp',
                                metadata:{ phone_number_id, display_phone_number },
                                contacts:[{ wa_id, profile:{name} }],
                                messages:[{ id, from, timestamp, type, text?:{body}, image?, document?, location?, interactive? }] },
                      field: 'messages' }] }] }
```
1. Verify the `X-Hub-Signature-256` HMAC using your app secret.
2. For each message: find/create customer by `wa_id` (this is the phone in international format). Upsert `conversations` keyed by `(workspace_id, channel='whatsapp', phone=wa_id)`.
3. For media (`image`, `document`, `audio`, `video`): call `GET /{media_id}` → returns a short-lived `url` → download (auth header with your token) → upload to your bucket.
4. Fan out WS.
5. For `message_status` callbacks (`sent|delivered|read|failed`): update the corresponding outbound `messages.delivered_at`, `read_at`.

**Sending**:
- Inside 24h window: free-form.
  ```
  POST https://graph.facebook.com/v20.0/{phone_number_id}/messages
  { messaging_product:'whatsapp', to: wa_id, type:'text', text:{ body, preview_url:false } }
  ```
- Outside 24h: must use an approved **template**.
  ```
  { type:'template', template:{ name:'order_update', language:{code:'en'}, components:[...] } }
  ```
- Attachments: upload first via `POST /{phone_number_id}/media` then send with `{ type:'image', image:{ id: uploaded_id } }`.

**Gotchas**:
- WhatsApp rejects URLs in text messages unless `preview_url:false` is set.
- The 24h session window starts at the **latest inbound** message. Track `last_inbound_at` on the conversation and bounce the UI to "template only" if expired.
- Meta's webhook retries aggressively on non-2xx — your handler must be idempotent (dedupe by `messages[].id`).

---

### 6.4 Instagram DM

**Goal**: direct messages, story replies, mentions from Instagram Business accounts (linked to a Facebook Page).

**One-time setup**:
1. Instagram account must be a **Business or Creator** account, connected to a Facebook Page.
2. Meta app with `instagram_basic`, `instagram_manage_messages`, `pages_manage_metadata`, `pages_messaging` permissions. App Review required before going live.
3. Subscribe the Page to webhook field `messages` (and `messaging_postbacks` for buttons).
4. Webhook URL + verify token (same pattern as WhatsApp).

**Inbound**: same Graph envelope as Messenger (uses the Page's webhook). The `entry[].messaging[].message.text` + `attachments[]` is your payload; sender id is a **PSID** (page-scoped user id) — you must call `GET /{psid}?fields=name,profile_pic` to get a display name.

**Send**:
```
POST https://graph.facebook.com/v20.0/me/messages?access_token=PAGE_TOKEN
{ recipient:{ id: psid }, message:{ text } }
```

**Gotchas**:
- 7-day messaging window (longer than WhatsApp). Outside it, only specific tags like `HUMAN_AGENT` work.
- PSIDs are Page-scoped — never show them to the user. Map to your `customers.id` on first sight.

---

### 6.5 Web widget (Live Chat)

**Goal**: embeddable JS widget + WebSocket chat.

**Shipped to customer site**:
```html
<script src="https://cdn.workspacex.com/widget.js" data-workspace="ws_123"></script>
```
The widget boots a WebSocket to `wss://api.workspacex.com/widget?workspace=ws_123&visitor=<uuid-stored-in-cookie>`.

**Backend**:
- On WS connect: register an anonymous `visitor_id` (first time) → create a `customers` row with `kind='visitor'` so tickets/conversations can reference it; enrich later when the visitor provides email/phone.
- Message envelope: `{ type: 'msg', text, attachments[] }` / `{ type: 'typing', on: bool }` / `{ type: 'read', messageId }`.
- Outbound agent messages push via the same WS.
- When the visitor leaves the page, mark the conversation `waiting` so the Inbox counter moves.
- File uploads: the widget uses `POST /uploads` (same presigned-URL endpoint as the agent side).

**Proactive prompts**: admin-configured triggers (URL matches, time on page >30s) fire a server-originated message via the WS.

---

### 6.6 Slack (internal escalations, not customer-facing here)

**Setup**:
1. Slack app with bot scopes `chat:write`, `channels:read`, `channels:history`, `users:read`.
2. Install per-workspace; store `bot_token`.
3. On ticket escalation → `chat.postMessage` to the configured channel with Block Kit card.
4. Interactive buttons (`Acknowledge`, `View in WorkspaceX`) send to `POST /webhooks/slack/interactions` — verify with signing secret.

---

### 6.7 Call recordings (Voice)

The UI shows a "Recordings" side panel per conversation. If/when telephony is added:
1. Use a provider (Exotel / Twilio / Plivo).
2. Webhook `call.completed` → download recording URL → push to bucket → attach to `conversations.recordings[]`.
3. Optional: send to Whisper / Gemini for transcription and store the transcript for search.

---

## 7. Postgres schema — full DDL

This is the authoritative schema. Use **Postgres 16** with extensions `pgcrypto` (gen_random_uuid, encrypt creds), `pg_trgm` (search), `vector` (pgvector for embeddings), `btree_gin` (for composite indexes).

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ---------- core tenancy ----------
CREATE TABLE workspaces (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  slug          text UNIQUE NOT NULL,
  timezone      text NOT NULL DEFAULT 'Asia/Kolkata',
  default_lang  text NOT NULL DEFAULT 'en',
  plan          text NOT NULL DEFAULT 'growth',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TYPE user_role AS ENUM ('admin','agent','viewer');
CREATE TYPE user_status AS ENUM ('online','away','offline');

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name           text NOT NULL,
  email          citext NOT NULL,
  password_hash  text,               -- null if oauth-only
  provider       text NOT NULL DEFAULT 'password', -- 'password'|'google'|'outlook'
  role           user_role NOT NULL DEFAULT 'agent',
  status         user_status NOT NULL DEFAULT 'offline',
  last_seen_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, email)
);
CREATE INDEX ON users (workspace_id, role);

CREATE TABLE auth_sessions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash   text NOT NULL,
  user_agent   text,
  ip           inet,
  expires_at   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON auth_sessions (user_id);
CREATE INDEX ON auth_sessions (token_hash);

-- ---------- customers & orders ----------
CREATE TABLE customers (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            text,
  phone           text,
  email           citext,
  shopify_id      text,
  psid            text,          -- Instagram/Messenger page-scoped id
  wa_id           text,          -- WhatsApp id (usually phone)
  since           date,
  address         jsonb,         -- {line1,line2,city,state,pincode,country}
  other_addresses jsonb,         -- array
  kind            text NOT NULL DEFAULT 'customer', -- 'customer'|'visitor'
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX customers_ws_phone_idx    ON customers (workspace_id, phone);
CREATE INDEX customers_ws_email_idx    ON customers (workspace_id, email);
CREATE INDEX customers_ws_shopify_idx  ON customers (workspace_id, shopify_id);
CREATE INDEX customers_ws_psid_idx     ON customers (workspace_id, psid);
CREATE INDEX customers_ws_waid_idx     ON customers (workspace_id, wa_id);
CREATE INDEX customers_name_trgm_idx   ON customers USING gin (name gin_trgm_ops);

CREATE TABLE orders (
  id            text PRIMARY KEY,      -- external id (shopify order id)
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id   uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  source        text NOT NULL,         -- 'shopify'|'unicommerce'|...
  placed_at     timestamptz NOT NULL,
  total         numeric(12,2) NOT NULL,
  currency      char(3) NOT NULL DEFAULT 'INR',
  status        text NOT NULL,
  items         jsonb NOT NULL,        -- [{name,sku,qty,price}]
  raw           jsonb,                 -- full upstream payload for debugging
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON orders (workspace_id, customer_id, placed_at DESC);

-- ---------- conversations & messages ----------
CREATE TYPE channel_kind AS ENUM ('whatsapp','instagram','live_chat','mail','call','slack','telegram','messenger','sms');
CREATE TYPE conversation_status AS ENUM ('open','pending','waiting','resolved','archived');

CREATE TABLE conversations (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id          uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  channel              channel_kind NOT NULL,
  external_thread_id   text,          -- gmail threadId / graph conversationId / WA wa_id / widget visitor
  assignee_id          uuid REFERENCES users(id),
  status               conversation_status NOT NULL DEFAULT 'open',
  preview              text,
  last_message_at      timestamptz,
  last_inbound_at      timestamptz,   -- drives WhatsApp 24h window UI
  unread_count         int NOT NULL DEFAULT 0,
  order_id             text REFERENCES orders(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, channel, external_thread_id)
);
CREATE INDEX conversations_ws_channel_last_idx ON conversations (workspace_id, channel, last_message_at DESC);
CREATE INDEX conversations_assignee_idx        ON conversations (workspace_id, assignee_id) WHERE assignee_id IS NOT NULL;
CREATE INDEX conversations_unassigned_idx      ON conversations (workspace_id) WHERE assignee_id IS NULL AND status IN ('open','pending');
CREATE INDEX conversations_preview_trgm_idx    ON conversations USING gin (preview gin_trgm_ops);

CREATE TYPE message_from AS ENUM ('customer','agent','ai','system');

CREATE TABLE messages (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  conversation_id      uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  from_role            message_from NOT NULL,
  from_user_id         uuid REFERENCES users(id),
  external_message_id  text,          -- gmail Message-ID / graph id / wa message id
  at                   timestamptz NOT NULL DEFAULT now(),
  text                 text,
  attachments          jsonb NOT NULL DEFAULT '[]',   -- [{kind,name,size,mime,url}]
  reply_to_id          uuid REFERENCES messages(id),
  delivered_at         timestamptz,
  read_at              timestamptz,
  raw                  jsonb,
  UNIQUE (workspace_id, conversation_id, external_message_id)
);
CREATE INDEX messages_conv_at_idx ON messages (conversation_id, at DESC);
CREATE INDEX messages_ws_at_idx   ON messages (workspace_id, at DESC);

CREATE TABLE conversation_notes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  author_id       uuid NOT NULL REFERENCES users(id),
  at              timestamptz NOT NULL DEFAULT now(),
  text            text NOT NULL
);
CREATE INDEX ON conversation_notes (conversation_id, at DESC);

-- ---------- tickets ----------
CREATE TYPE ticket_status AS ENUM ('queued','assigned','in_progress','blocked','resolved','closed');

CREATE TABLE tickets (
  id                  bigserial PRIMARY KEY,   -- display as "#{id}"
  workspace_id        uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id         uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  conversation_id     uuid REFERENCES conversations(id),
  order_id            text REFERENCES orders(id),
  parent_ticket_id    bigint REFERENCES tickets(id),
  status              ticket_status NOT NULL DEFAULT 'queued',
  source              channel_kind NOT NULL,
  channel             channel_kind NOT NULL,
  task                text NOT NULL,                -- 'delay_delivery' etc
  team                text NOT NULL,
  assignee_id         uuid REFERENCES users(id),
  priority            text NOT NULL DEFAULT 'normal',
  subject             text,
  description         text,
  tags                text[] NOT NULL DEFAULT '{}',
  custom_fields       jsonb NOT NULL DEFAULT '{}',
  voc                 text,
  resolution_notes    text,
  sla_due_at          timestamptz,
  order_status        text,
  order_placed_on     date,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  resolved_at         timestamptz,
  -- embedding for similar-ticket search
  subject_embedding   vector(1536)
);
CREATE INDEX tickets_ws_status_idx   ON tickets (workspace_id, status, created_at DESC);
CREATE INDEX tickets_ws_assignee_idx ON tickets (workspace_id, assignee_id) WHERE assignee_id IS NOT NULL;
CREATE INDEX tickets_sla_idx         ON tickets (workspace_id, sla_due_at) WHERE status NOT IN ('resolved','closed');
CREATE INDEX tickets_customer_idx    ON tickets (customer_id, created_at DESC);
CREATE INDEX tickets_subj_trgm_idx   ON tickets USING gin (subject gin_trgm_ops);
CREATE INDEX tickets_embedding_idx   ON tickets USING hnsw (subject_embedding vector_cosine_ops);

CREATE TABLE ticket_timeline (
  id          bigserial PRIMARY KEY,
  ticket_id   bigint NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL DEFAULT now(),
  actor       text NOT NULL,   -- 'system' or user email
  actor_user_id uuid REFERENCES users(id),
  text        text NOT NULL
);
CREATE INDEX ON ticket_timeline (ticket_id, at DESC);

CREATE TABLE ticket_notes (
  id         bigserial PRIMARY KEY,
  ticket_id  bigint NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  author_id  uuid NOT NULL REFERENCES users(id),
  at         timestamptz NOT NULL DEFAULT now(),
  text       text NOT NULL
);
CREATE INDEX ON ticket_notes (ticket_id, at DESC);

-- ---------- teams (internal chat) ----------
CREATE TABLE teams_channels (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text,
  privacy       text NOT NULL DEFAULT 'private',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE teams_members (
  channel_id  uuid NOT NULL REFERENCES teams_channels(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  joined_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_id, user_id)
);

CREATE TABLE teams_dms (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  member_ids    uuid[] NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE teams_messages (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        uuid NOT NULL REFERENCES workspaces(id),
  channel_id          uuid REFERENCES teams_channels(id) ON DELETE CASCADE,
  dm_id               uuid REFERENCES teams_dms(id) ON DELETE CASCADE,
  user_id             uuid NOT NULL REFERENCES users(id),
  at                  timestamptz NOT NULL DEFAULT now(),
  blocks              jsonb NOT NULL,      -- [{type:'text'|'mention',...}]
  attachments         jsonb NOT NULL DEFAULT '[]',
  reply_to_id         uuid REFERENCES teams_messages(id),
  CHECK ((channel_id IS NOT NULL) <> (dm_id IS NOT NULL))
);
CREATE INDEX ON teams_messages (channel_id, at DESC);
CREATE INDEX ON teams_messages (dm_id, at DESC);

CREATE TABLE teams_reactions (
  message_id  uuid NOT NULL REFERENCES teams_messages(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  emoji       text NOT NULL,
  PRIMARY KEY (message_id, user_id, emoji)
);

CREATE TABLE teams_read_cursors (
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel_id       uuid REFERENCES teams_channels(id) ON DELETE CASCADE,
  dm_id            uuid REFERENCES teams_dms(id) ON DELETE CASCADE,
  last_read_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, channel_id, dm_id)
);

-- ---------- notifications ----------
CREATE TABLE notifications (
  id          bigserial PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        text NOT NULL,   -- assign|reply|sla|broadcast|member
  title       text NOT NULL,
  body        text,
  link        text,
  ts          timestamptz NOT NULL DEFAULT now(),
  unread      boolean NOT NULL DEFAULT true
);
CREATE INDEX ON notifications (user_id, ts DESC);
CREATE INDEX ON notifications (user_id) WHERE unread;

-- ---------- platforms / integrations / apis ----------
CREATE TABLE platform_credentials (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind           text NOT NULL,   -- 'gmail'|'outlook'|'whatsapp'|'instagram'|'shopify'|...
  status         text NOT NULL DEFAULT 'connected',
  config         jsonb NOT NULL DEFAULT '{}',
  secret_enc     bytea,           -- pgp_sym_encrypt(secret, workspace_key)
  history_cursor text,            -- gmail historyId / graph deltaLink / shopify since
  expires_at     timestamptz,
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON platform_credentials (workspace_id, kind);

CREATE TABLE ecommerce_stores (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  platform        text NOT NULL,
  name            text NOT NULL,
  credentials_id  uuid REFERENCES platform_credentials(id),
  health          int NOT NULL DEFAULT 100,
  orders_30d      int NOT NULL DEFAULT 0,
  last_sync_at    timestamptz,
  status          text NOT NULL DEFAULT 'connected'
);

CREATE TABLE integrations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text,
  kind          text NOT NULL DEFAULT 'custom',
  config        jsonb NOT NULL DEFAULT '{}',
  health        text NOT NULL DEFAULT 'good'
);

CREATE TABLE apis (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id  uuid REFERENCES integrations(id) ON DELETE SET NULL,
  name            text NOT NULL,
  method          text NOT NULL,
  description     text,
  spec            jsonb NOT NULL,       -- request shape, auth, URL, headers, json-schema of args
  last_used_at    timestamptz
);

CREATE TABLE api_logs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  api_id        uuid REFERENCES apis(id) ON DELETE SET NULL,
  customer_id   uuid REFERENCES customers(id),
  trace_id      text NOT NULL,
  status        int NOT NULL,
  latency_ms    int NOT NULL,
  request       jsonb,
  response      jsonb,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON api_logs (workspace_id, at DESC);
CREATE INDEX ON api_logs (api_id, at DESC);

-- ---------- sheets ----------
CREATE TABLE sheets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  url           text NOT NULL,
  name          text,
  tabs          jsonb NOT NULL DEFAULT '[]',
  rows          int NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'connected',
  last_sync_at  timestamptz
);

-- ---------- agents ----------
CREATE TABLE agents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  model         text NOT NULL,              -- 'claude-opus-4-7' etc
  persona       text NOT NULL,
  instructions  text NOT NULL,
  temperature   numeric(3,2) NOT NULL DEFAULT 0.3,
  tools         jsonb NOT NULL DEFAULT '[]', -- [{key,enabled,config}]
  is_active     boolean NOT NULL DEFAULT true
);

-- KB chunks powering the 'kb_search' tool + similar-ticket search
CREATE TABLE kb_chunks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  source        text NOT NULL,              -- 'sheet:<id>' | 'url:<...>' | 'macro:<id>'
  title         text,
  body          text NOT NULL,
  embedding     vector(1536) NOT NULL
);
CREATE INDEX kb_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);

-- ---------- health / incidents ----------
CREATE TABLE health_services (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  name           text NOT NULL,
  kind           text NOT NULL,
  status         text NOT NULL DEFAULT 'operational',
  uptime_30d     numeric(5,2) NOT NULL DEFAULT 100,
  latency_p95_ms int,
  error_rate     numeric(5,2) NOT NULL DEFAULT 0,
  last_check_at  timestamptz
);

CREATE TABLE incidents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  title         text NOT NULL,
  status        text NOT NULL,
  tone          text NOT NULL,
  components    text[] NOT NULL,
  started_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz,
  summary       text
);

-- ---------- outbox / delivery ----------
-- For reliable webhooks / channel deliveries: write-ahead, worker picks up.
CREATE TABLE outbox (
  id           bigserial PRIMARY KEY,
  kind         text NOT NULL,  -- 'send_wa'|'send_gmail'|'send_outlook'|'send_ig'|'webhook'
  payload      jsonb NOT NULL,
  status       text NOT NULL DEFAULT 'pending',
  attempts     int NOT NULL DEFAULT 0,
  not_before   timestamptz NOT NULL DEFAULT now(),
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  locked_by    text,
  locked_at    timestamptz
);
CREATE INDEX outbox_due_idx ON outbox (status, not_before) WHERE status = 'pending';
```

**Migration tool**: use **golang-migrate**, **Prisma Migrate**, or **Alembic** — whichever matches your stack. Keep migrations in `db/migrations/` and run them in CI before rolling out.

---

## 8. Realtime — making the UI feel instant

The current UI already animates optimistically (message appears in the bubble on Enter). The backend has to preserve that feel.

**Transport**: a single **WebSocket** per logged-in browser tab at `wss://api.workspacex.com/ws`, authenticated with the session cookie. Server-Sent Events is a fallback for old proxies.

**Subscription model** — the client sends:
```
{ "action": "subscribe", "topics": [
  "inbox:ws_123",                 // all conversation list updates for this workspace
  "conv:<uuid>",                  // when a conversation is open
  "ticket:<id>",                  // when a ticket is open
  "teams:<channel_id>",           // when a teams channel is open
  "notif:<user_id>"               // always
] }
```
The server only pushes events for topics the tab subscribed to — keeps bandwidth tight.

**Event envelope**:
```
{ "type": "message.created",
  "topic": "conv:abc",
  "at":   "2026-04-22T12:42:00Z",
  "data": { ...message row... } }
```

**Events produced**:
- `message.created`, `message.updated` (delivery ticks), `message.read`
- `conversation.updated` (assignee change, status change, preview/unread moved)
- `ticket.created`, `ticket.updated`, `ticket.assigned`, `ticket.timeline.appended`
- `notification.created`
- `team.message.created`, `team.reaction.changed`, `team.typing`
- `presence.changed`
- `sla.breach_warning`
- `platform.health.changed`

**Implementation**: in Postgres, use `LISTEN/NOTIFY` inside triggers on `messages`, `tickets`, `notifications`. A thin fan-out service (Go / Node) holds the WS connections; a single `LISTEN` per pod receives payloads and pushes to subscribed sockets. For larger deploys, move the fan-out to Redis Pub/Sub (`SUBSCRIBE topic-*`) and keep Postgres just as the source of truth.

**Optimistic UI contract** (important):
- When the client POSTs a message, the server returns the **persisted message row** immediately with the same client-generated `temp_id`. Client replaces the local bubble with the server row. This is already how the React code is shaped (the bubble appears before `send()` resolves).
- Every mutation endpoint echoes the full resulting entity (not just `{ok:true}`), so the client has no reason to refetch.

---

## 9. Performance & caching — matching the current snappy UX

The frontend renders list + detail panes without lag. Hitting the same feel against a real DB requires a handful of specific moves.

**Indexes** — already in the DDL above. In particular:
- `conversations (workspace_id, channel, last_message_at DESC)` → inbox list.
- `messages (conversation_id, at DESC)` → chat body pagination.
- `tickets (workspace_id, status, created_at DESC)` → ticket list.
- `tickets USING hnsw (subject_embedding vector_cosine_ops)` → similar-ticket search in < 30 ms.
- `pg_trgm` indexes for every "ILIKE" search the UI does.

**Pagination**: **cursor-based only**, never `OFFSET`. Cursor = `(last_message_at, id)` tuple encoded base64. The UI already shows "1-20 of 265" placeholders — return `total` from a separate cached counter (see next).

**Counts & KPIs**: the Inbox tabs show `Pending 263 | Unassigned 49`, Analytics shows "842 tickets this week", etc. Never run `COUNT(*)` on every render.
- Maintain **materialized counters** via trigger: `workspace_counters(workspace_id, kind, value)` incremented on insert/update. Flush every N seconds to a Redis key for hot reads.
- For historical charts, pre-aggregate into `analytics_daily(workspace_id, day, metric, value)` nightly.

**Read replicas**: all `GET` endpoints go to a read replica; writes to primary. Use session-pinning for 500ms after a write so the user sees their own edit.

**Caching layer (Redis)**:
- `me:<user_id>` — user + workspace + role, 5 min TTL, busted on `PATCH /me`.
- `unread:<user_id>` — counts for the TopBar badge.
- `ticket:<id>` — hot ticket detail, 60s TTL, busted on any write via NOTIFY.
- `conv:<id>:messages:head` — first page of messages for open conversations.
- Rate-limiter buckets for inbound webhooks.

**N+1 prevention**: the ticket list card pulls `channel, id, customer, task, assignee, status, sla`. Build it with a single `SELECT` joined to `customers` (use `LATERAL` joins for `users` assignee names). Same for conversations list.

**Connection pooling**: **PgBouncer** in transaction mode in front of Postgres. Keep max DB connections proportional to cores (typically `cores * 4`).

**Attachment URLs**: short-lived signed URLs (5 min) for images, longer (1 hour) for docs. Never proxy binary through your API — always 302 to the signed URL or embed the signed URL in the response.

**Bulk endpoints**: the API Logs page shows 48 rows at a time and filters across hundreds of thousands. Add `GET /api-logs/stream` (NDJSON) for exports.

**SLA frontend feel**: the SLA chip shows "5d 8h" client-side — that value is computed at read time from `sla_due_at - now()`. Keep the SLA engine as a lightweight worker that only writes on state changes, not every minute.

---

## 10. AI reply pipeline — the heart of the product

When a new inbound `message.created` lands in a conversation that has no active agent response in-flight:

1. **Context assembly** (< 100 ms):
   - Load last 20 messages from the conversation.
   - Load the customer profile, recent orders, open tickets.
   - Build `AgentCtx { workspace_id, conversation_id, customer_id, channel, trace_id }`.
2. **Agent selection** — first non-null wins:
   1. `workspace.ai_force_default == true` → **built-in `default-orchestrator`** (§3.11.H).
   2. Active published Langflow agent (`SELECT … FROM agents WHERE status='published' ORDER BY last_published_at DESC LIMIT 1`) → run that (§3.11.D).
   3. Otherwise → **built-in `default-orchestrator`**.
   The built-in agent is always the fallback — the pipeline is never "no agent".
3. **Run**:
   - **Langflow path**: `POST /api/v1/run/<flow_id>?stream=true` with `session_id = conversation_id`. Tool calls go out via the WorkspaceX custom component → our `/internal/apis/:name` endpoints.
   - **Default path**: `pydantic-ai` `orchestrator.run_stream(user_message, deps=ctx)`. Tools are Python functions with Pydantic-validated args/returns; the tool loop is handled by the library.
4. **Streaming**: every text delta and tool event SSE-streams into the WS fan-out → `conv:<id>` subscribers see the AI response type out live.
5. **Confidence routing**:
   - Agent called `send_reply` → delivered on the channel adapter (via `outbox.kind='send_xxx'`).
   - Agent called `escalate_to_human` → ticket assigned + terse system reply sent; the "AI Draft" chip stays off because a human now owns it.
   - Neither → persist as a `messages` row with `from_role='ai'`, `delivered_at=null`, and surface the **AI Draft** chip so an agent can 1-click send.
6. **Guardrails** (enforced in code, not prompt — see §3.11.H):
   - Token ceiling (~16k in, ~2k out).
   - Per-tool caps (refund ≤ ₹10,000, etc.).
   - Per-customer rate limit (20 runs/hour).
   - Loop guard (>8 tool calls → force-escalate).
   - Consecutive-error guard (3 tool errors → escalate).
7. **Record**: one `agent_runs` row with `version` (0 = built-in, N = Langflow snapshot), `trace jsonb`, tokens, cost. Every tool call also writes an `api_logs` row.

**Embedding worker**: whenever a ticket is created or its subject changes, enqueue an embedding job → update `tickets.subject_embedding` with Voyage/OpenAI embeddings (1536-dim). Same for KB chunks. Powers the similar-ticket panel and the `kb_search` / `similar_tickets` tools.

**Prompt caching**: the system prompt + tool schema are stable for both agent paths — use Anthropic **prompt caching** to cut cost ~80%. Re-cache when the admin saves the agent (Langflow) or when the service deploys (built-in).

---

## 11. Background workers

Run these as separate processes (or goroutines / BullMQ queues):

| Worker | Trigger | What it does |
|---|---|---|
| **Inbound fan-in** | Webhooks | Normalizes Gmail/Outlook/WhatsApp/IG/widget events → `messages` + WS push |
| **AI responder** | On `message.created` from customer | Runs pipeline in §10 |
| **Outbox dispatcher** | Every 500ms | Sends pending `outbox` rows via the right adapter, retries with backoff |
| **SLA engine** | Every 30s | Finds tickets nearing `sla_due_at`, emits warnings |
| **Watch renewer** | Daily cron | Re-ups Gmail `users.watch`, Outlook subscription, Shopify webhook topics |
| **Shopify sync** | Shopify webhook + hourly backfill | Upserts `orders`, `customers` |
| **Sheet sync** | Every 5 min per sheet | Pulls rows, updates `kb_chunks` embeddings when changed |
| **Health prober** | Every 30s | Pings each integration, updates `health_services`, opens `incidents` |
| **Analytics aggregator** | Nightly | Rolls up `analytics_daily` |
| **Embedding indexer** | On ticket / KB write | Updates vector columns |
| **Presence reaper** | Every 60s | Marks users offline if no heartbeat for 2 min |

Use **Postgres advisory locks** (`pg_try_advisory_lock`) so each worker runs singleton without extra infra; or move to Redis-backed queues as you scale.

---

## 12. API surface summary (all endpoints so far)

Auth: `POST /auth/signup|signin|signout|forgot-password`, `POST /auth/oauth/:provider`, `GET /auth/me`.

Me: `GET/PATCH /me`, `GET /me/unread-counts`, `POST /me/password`, `POST /me/2fa/*`, `GET/PATCH /me/notification-prefs`.

Workspace: `GET/PATCH /workspace`, `GET /workspace/members`.

Search: `GET /search?q=&limit=`.

Conversations: `GET /conversations`, `GET /conversations/:id`, `POST /conversations/:id/messages`, `POST /conversations/:id/notes`, `POST /conversations/:id/typing`, `POST /messages/:id/read`, `GET /customers/:id/recordings`, `GET /channels/for-customer/:id`.

Uploads: `POST /uploads` → presigned URL.

Tickets: `GET /tickets`, `GET /tickets/:id`, `POST /tickets`, `PATCH /tickets/:id`, `POST /tickets/:id/notes`, `POST /tickets/:id/child`, `PATCH /tickets/:id/child`, `POST /tickets/:id/resolve`, `POST /tickets/similar`, `POST /tickets/:id/merge`.

Customers: `GET /customers`, `GET /customers/:id`, `GET /customers/search`, `POST /customers`, `PATCH /customers/:id`.

Teams: `GET /teams/members`, `GET/POST /teams/channels`, `GET/POST /teams/dms`, `GET /teams/conversations/:id/messages`, `POST /teams/conversations/:id/messages`, `POST /teams/messages/:id/reactions`, `POST /teams/typing/:id`, `GET /presence`.

Notifications: `GET /notifications`, `POST /notifications/:id/read`, `POST /notifications/read-all`, `DELETE /notifications/:id`, `DELETE /notifications`.

Platforms: `GET /platforms`, `POST /platforms/:id/connect`, `POST /platforms/:id/disconnect`, `GET /platforms/stats`.

Channels > E-Commerce: `GET/POST /ecommerce/stores`, `DELETE /ecommerce/stores/:id`, `POST /ecommerce/stores/:id/sync`, `GET /ecommerce/events`.

Channels > APIs: `GET/POST /apis`, `PATCH /apis/:id`, `POST /apis/:id/clone`, `DELETE /apis/:id`, `POST /apis/:id/test`.

Channels > Integrations: `GET/POST /integrations`, `DELETE /integrations/:id`.

Channels > Logs: `GET /api-logs`, `GET /api-logs/:id`, `GET /api-logs/stats`, `GET /api-logs/stream`.

Channels > Sheets: `GET /sheets`, `POST /sheets/validate`, `POST /sheets`, `PATCH /sheets/:id`, `DELETE /sheets/:id`.

Channels > Health: `GET /health/services`, `GET /health/services/:id/uptime`, `GET /incidents`, `POST /incidents/subscribe`.

Analytics: `GET /analytics/summary`, `GET /analytics/timeseries`.

Agents: `GET/POST /agents`, `GET /agents/:id`, `PATCH /agents/:id`, `DELETE /agents/:id`, `POST /agents/:id/test`.

Billing: `GET /billing/plan`, `GET /billing/usage`, `POST /billing/checkout`, `GET /billing/invoices`.

Webhooks (public, signature-verified): `POST /webhooks/gmail`, `/webhooks/outlook`, `/webhooks/whatsapp`, `/webhooks/instagram`, `/webhooks/shopify`, `/webhooks/slack/events`, `/webhooks/slack/interactions`.

---

## 13. Deployment / infra sketch

- **API**: Go (Gin/Fiber) or Node (NestJS/Fastify). Horizontal, stateless.
- **DB**: Postgres 16 on RDS/CloudSQL with read replica. PgBouncer.
- **Cache / pubsub**: Redis 7.
- **Object storage**: S3 or GCS with CloudFront/Cloud CDN signed URLs.
- **Workers**: same binary as API, launched with `--role=worker`; uses advisory locks to partition tasks.
- **Message bus for webhooks**: Cloud Pub/Sub (you already need it for Gmail). Re-use for Shopify webhooks etc.
- **Secrets**: AWS Secrets Manager / GCP Secret Manager; never in env files in prod.
- **Observability**: OpenTelemetry everywhere. Structured JSON logs with `trace_id` matching the one shown in the API Logs UI. Ship to Grafana Loki / Datadog.
- **CI/CD**: GitHub Actions → Docker → ECS/Fargate or GKE. Migrations run as an init container.

---

## 14. Non-goals (don't build yet)

The UI has placeholders that should **not** be considered requirements:
- Analytics charts area renders "Charts coming soon" — ship the 4 KPIs first, charts later.
- Tickets `Conversation | Notes | Customer` tabs are placeholders — ship `Info` only initially.
- Preferences > Animations is 100% client-side — no backend.
- Billing page numbers are decorative — wire to Stripe last.

Start at the top of Section 5 and walk down.
