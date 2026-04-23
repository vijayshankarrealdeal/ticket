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

Backend:
- [ ] `POST /auth/signup` → `{ name, email, password }` → `{ user, token }`
- [ ] `POST /auth/signin` → `{ email, password }` → `{ user, token }`
- [ ] `POST /auth/oauth/google` + `POST /auth/oauth/outlook` (authorization-code flow)
- [ ] `POST /auth/forgot-password` → email magic link
- [ ] `POST /auth/signout`
- [ ] `GET /auth/me` to rehydrate session

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
**UI**: single agent config — name, model (Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5), persona, system instructions, temperature slider, tool toggles (KB search, Order lookup, Issue refund, Escalate, Web search, Shipping status).

Backend:
- [ ] `GET /agents`, `POST /agents`, `GET /agents/:id`, `PATCH /agents/:id`, `DELETE /agents/:id`.
  - Shape: `{ id, name, model, persona, instructions, temperature, tools: [{ key, enabled, config? }] }`.
- [ ] `POST /agents/:id/test` → runs a message through the agent with tool access.
- [ ] **Tool runtime** (the executor): given a selected tool, call the configured API (from API Library) with the agent's arguments, capture the trace (API Logs), return result to the model.
- [ ] **KB search** tool: vector store over help articles / macros; likely the same infra as the similar-tickets search.
- [ ] **Refund** tool: bounded (`up to ₹10,000` in the UI) — enforce server-side with approval hooks.
- [ ] **Escalate** tool: creates a ticket + reassigns, then replies "I've looped in a human".

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

## 5. Priority order (suggested)

1. Auth + Workspaces + Users/Roles
2. Customers + Orders ingestion (Shopify connector) — tickets are useless without customer data
3. Conversations ingestion for **one** channel end-to-end (start with WhatsApp), including the WebSocket push
4. Tickets CRUD, timeline, assignment, SLA engine
5. AI reply loop (Agent Builder + tool runtime + API Library)
6. Notifications + Teams (can run in parallel after #4)
7. Analytics, Health, Sheets, remaining channels
8. Billing
