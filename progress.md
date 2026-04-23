# Progress

Last updated: 2026-04-23 — Sixth round of end-to-end wiring closed every outstanding channel connector, OAuth sign-in, billing surface and widget embed: **channel webhooks** at `/webhooks/{whatsapp,gmail,outlook,instagram,shopify,slack,voice,stripe}` all funnel through a new shared `ingest_inbound_message` helper in [conversations.py](backend/app/routers/conversations.py) so the Stage-1/Stage-2 classifier, the WS fan-out (`message.created`, `conversation.updated`, `ticket.timeline.appended`, `notif:<user>`) and the AI auto-reply behave identically to the auth'd simulator; **real outbox adapters** for `send_wa` (Meta Cloud API), `send_gmail` (Gmail API + SMTP fallback), `send_outlook` (Graph `/me/sendMail`), `send_ig` (Instagram Graph), `send_slack` (Block Kit `chat.postMessage`) and `send_sms` (Twilio) run through credentials loaded from a new `platform_credentials` table; **OAuth sign-in + channel connect** in [routers/oauth.py](backend/app/routers/oauth.py) wires `GET /auth/oauth/{google,outlook}/start` → provider consent → callback → session issue + `platform_credentials` upsert (dev-mode short-circuits when no `{PROVIDER}_CLIENT_ID` so offline demos keep working); full **Stripe billing** at `/billing/{plan,usage,invoices,checkout,portal}` with live counter math + demo-invoice fallback; a **drop-in widget embed** served at `/widget.js?workspace=<slug>` that mounts a launcher + chat panel + proactive prompt on any third-party site, using the existing `/widget/ws` realtime socket; a **Settings → Members** panel + `POST /workspace/members` invite flow surfacing the reset token until SMTP ships; **CSAT outbound delivery** (`POST /tickets/:id/csat/send` enqueues an email or WA template to the outbox so the survey link actually reaches the customer); and **AuthPage OAuth buttons** that redirect through the new start endpoints and finalize the session on `/#/oauth-landing?token=…` return.

Earlier: Fifth round of end-to-end wiring closed nine more spec-§5 backlog items: full **Google Sheets connector** (`GET/POST/PATCH/DELETE/sync` against `sheets`, sync writes to `kb_chunks` so the orchestrator's `kb_search` tool sees the rows), per-team **SLA policies** CRUD (`/workspace/sla-policies`) wired into the `_sla_hours` resolver `POST /tickets` already calls, **2FA TOTP** (`/me/2fa/setup|verify|disable` + Security UI, RFC-6238 verify with ±1 step skew), real **outbox adapters** for `email` (aiosmtplib when `SMTP_HOST` is set, falls back to `delivery_log`) and `webhook` (httpx POST) with secret-redacted records on every send, a `delivery_log` table + `/workspace/delivery-log` admin endpoint and Settings → Deliveries panel, **channel mentions** in Teams (`#channel-name` parsed alongside `@user` and emits `team.channel.mentioned` to the referenced channel's WS topic), **real HTTP probes** in the health-prober worker when an integration's `config.ping_url` is set (smoothed p95 + decaying error rate), a permanent **embedding indexer** worker with a deterministic hashed-bag-of-words fallback (`backend/app/ai/embeddings.py`) backfilling `kb_chunks.embedding` + `tickets.subject_embedding`, and a full **web widget** (`/widget/bootstrap` + `/widget/messages` + `wss:/widget/ws`) that bootstraps a `widget_visitors` row per browser session, opens a `customers.kind='visitor'` conversation, and routes inbound through the same `_run_inbound_ai_reply` orchestrator path as the authed inbound endpoint.

Earlier: Fourth round of end-to-end wiring: inbound customer messages now trigger an AI auto-reply that streams into the live conversation (`ai.typing.started` → `ai.delta` chunks → `message.created` with `streamFinal`), the inbound pipeline picks the workspace's preferred agent via `select_inbound_agent` (langflow-published → published builtin → default-orchestrator; honours `workspace.ai_force_default`), a background **health prober worker** auto-updates `health_services` every 60 s and opens/resolves `incidents` on threshold breach, `analytics_daily` + `workspace_counters` tables are refreshed by dedicated worker loops, the idempotency ledger has a 24 h cleanup sweep, admins can now broadcast workspace-wide announcements (`POST /workspace/broadcast` fans a `broadcast` notification out to every member), `POST /workspace/members` invites a teammate and pushes `teammate_joined` notifications to the rest of the workspace, `DELETE /me/oauth/:provider` handles account unlinking with a guard against locking yourself out, `/auth/forgot-password` + `/auth/reset-password` implement the single-use token flow (dev mode echoes the token so the AuthPage's "Forgot password?" link actually round-trips), `/me/unread-counts` now returns real `teamsUnread` derived from `teams_read_cursors`, and Conversations renders a dedicated email-thread variant for `email`/`gmail`/`outlook` channels.

Earlier: Third round of end-to-end wiring — Teams chat now round-trips through the backend (messages POST, reactions POST + `team.reaction.updated` WS, typing POST, read cursors, heartbeat + /teams/presence refresh, workspace presence WS, @mentions → `mention` notifications), customer-reply notifications land on the ticket's assignee from the `POST /conversations/:id/inbound` path, conversation notes save via `POST /conversations/:id/notes`, the Conversations composer has a "Simulate inbound" button that drives the intent classifier live, the Health page re-run button calls `POST /health/probe` (bumps `last_check_at` + p95), and the intent classifier escalates ambiguous Stage-1 results to Stage-2 LLM when `OPENAI_API_KEY` is configured (falls back cleanly otherwise).

Earlier: Second round of wiring — inbound message pipeline (`POST /conversations/:id/inbound` → intent classifier → attach/reopen/create), `POST /ai/draft` for composer-side AI suggestions, idempotency keys on tickets + messages, RBAC (`AgentOrAdmin` / `AdminOnly`) enforced across mutation routes, public CSAT survey flow (`/csat/:token`), merge/split/attach-message modals replacing `prompt()`, Platforms page wired to `include=all` catalog with Manage/OAuth-stub modal, ApiLogs page fixed against real `/api-logs/:id` detail shape.

Earlier: Build 2 backend surface partially landed (API Library, background workers, intent classifier Stage 1, default orchestrator, workspace + notification-prefs, split/merge/attach, channel switcher, Analytics timeseries) and the frontend stubs flagged in the previous audit (Settings save, AgentBuilder save/test, Tickets detail tabs, Teams realtime, Analytics chart, upload PUT) are wired.

Status against [BACKEND_SPEC.md](BACKEND_SPEC.md). Frontend progress lives in this file too (it grew out of the original SPA log). Wiring contract between frontend and backend is in [wire.md](wire.md).

---

## What's done

### Backend — Build 1 spine (Auth ↔ Conversation ↔ Ticket)

FastAPI service at [backend/](backend/). **Stack: Postgres 16 + asyncpg + MinIO + in-process WebSocket broker.** `docker compose up -d db minio redis api worker` brings everything online; on first boot the api container auto-applies [backend/app/schema.sql](backend/app/schema.sql) and seeds a demo workspace, and ensures the MinIO bucket exists.

- **Auth** ([backend/app/routers/auth.py](backend/app/routers/auth.py)) — Argon2id password auth, 32-byte bearer tokens stored as `sha256(token)` in `auth_sessions`, `GET /auth/me`, `POST /auth/signout`, `wx_session` cookie mirror. OAuth endpoints short-circuit for Build 1; recipes stand ready in spec §3.1.
- **Me + workspace** ([backend/app/routers/me.py](backend/app/routers/me.py)) — `GET/PATCH /me`, `GET /me/unread-counts`, `GET /workspace`, `PATCH /workspace` (admin-only), `GET /workspace/members`, `GET/PATCH /me/notification-prefs`, `POST /me/password` (Argon2id verify + rehash).
- **Customers** ([backend/app/routers/customers.py](backend/app/routers/customers.py)) — list, detail (with `recentOrders` + `tickets[]`), search, create, patch, `GET /customers/:id/recordings`, `GET /channels/for-customer/:id` (returns the set of channels a customer is reachable on so the Conversations header can hop between them).
- **Conversations + messages** ([backend/app/routers/conversations.py](backend/app/routers/conversations.py))
  - `GET /conversations` (channel/tab/q filters), `GET /conversations/:id` (messages + customer + orders + tickets + notes all nested in the shape [src/pages/Conversations.jsx](src/pages/Conversations.jsx) renders).
  - `POST /conversations/:id/messages` persists the agent reply, fans out `message.created` on `conv:<id>` and `conversation.updated` on `inbox:<ws>`, appends `Agent X replied on <channel>` to every linked ticket's timeline.
  - `POST /conversations/:id/notes`, `POST /conversations/:id/typing`, `POST /messages/:id/read`, `GET /conversations/:id/tickets`.
- **Tickets** ([backend/app/routers/tickets.py](backend/app/routers/tickets.py)) — list with filter/sort/q, detail with nested `customerDetails/timeline/notes/order/customFields/pendingChild/conversationId`, `POST /tickets` (auto-creates a guest customer when needed), `PATCH /tickets/:id` (writes timeline entries + notifies new assignee), `POST /tickets/:id/notes`, `POST/PATCH /tickets/:id/child`, `POST /tickets/:id/resolve`, `POST /tickets/:id/merge`, `POST /tickets/:id/split`, `POST /tickets/:id/attach-message`, `POST /tickets/similar` (ILIKE; pgvector HNSW later). Stage-1 intent classifier at `POST /internal/intent/classify` (deterministic keyword + order-ref router, spec §3.4.1 "What to keep simple in Build 1").
- **Teams internal chat** ([backend/app/routers/teams.py](backend/app/routers/teams.py)) — members, channels, DMs, messages, reaction toggle (with `team.reaction.updated` WS fan-out), typing broadcasts, `GET /teams/presence`, `POST /teams/presence/heartbeat`, `POST /teams/conversations/:id/read` read cursors, `GET /teams/unread` per-thread badges, `@Name` mention parser on write path that emits `mention` notifications.
- **Notifications** ([backend/app/routers/notifications.py](backend/app/routers/notifications.py)) — list/mark-read/read-all/dismiss/clear plus a helper any other route can call to push a typed notification to a user.
- **Global search** ([backend/app/routers/search.py](backend/app/routers/search.py)) — `GET /search?q=&limit=` grouped by conversations/tickets/contacts (case-insensitive ILIKE).
- **Platforms + stats** ([backend/app/routers/platforms.py](backend/app/routers/platforms.py)) — slug-keyed catalog merged with per-workspace state; list, stats, connect, disconnect (ON CONFLICT upsert).
- **Analytics** ([backend/app/routers/analytics.py](backend/app/routers/analytics.py)) — `/summary` returns real `ticketsThisWeek` + `avgResolution` (mean `resolved_at - created_at`); `/timeseries?metric=tickets|resolution&range=Nd` queries `tickets` grouped by day (CSAT + AI deflection still placeholder until survey + run ledger wire in).
- **Agents** ([backend/app/routers/agents.py](backend/app/routers/agents.py)) — CRUD + `POST /agents/:id/test` now runs the default orchestrator, `GET /agents/:id/runs` returns trace history.
- **Default orchestrator** ([backend/app/ai/orchestrator.py](backend/app/ai/orchestrator.py)) — `pydantic-ai` typed-tool agent with tools `find_customer` / `get_order` / `list_customer_orders` / `similar_tickets` / `send_reply` / `issue_refund` (₹10k cap) / `escalate_to_human`. Falls back to a deterministic heuristic responder when `OPENAI_API_KEY` or `pydantic-ai` isn't installed so the demo works offline. Every run writes an `agent_runs` row + `send_reply` enqueues via `outbox` and fans out on `conv:<id>`.
- **API Library + Integrations + API Logs** ([backend/app/routers/channels.py](backend/app/routers/channels.py)) — full CRUD for `apis` / `integrations` / `ecommerce_stores`, paginated `GET /api-logs` with status-class + api filters, `GET /api-logs/:id` detail view, rolling-24h `GET /api-logs/stats`. `POST /internal/apis/:name` is the internal tool-call executor the orchestrator (and, later, Langflow custom nodes) hit — every call records an `api_logs` row so the Channels → API Logs page shows real traffic.
- **Channels E-commerce stubs** — `ecommerce/stores` connect / delete / sync, events feed stub. Shopify adapter + real activity feed land with Build 2. Sheets / Health / Incidents are lean stubs until their connectors ship.
- **Uploads → MinIO** ([backend/app/routers/uploads.py](backend/app/routers/uploads.py) + [backend/app/s3.py](backend/app/s3.py)) — `POST /uploads` returns a real presigned S3-v4 URL against MinIO. Smoke-tested PUT → 200 → GET → 200. Rewrites the host to `S3_PUBLIC_URL` so a browser on `:3000` can reach the same object the api uses via `minio:9000`.

### Backend — realtime

- **WebSocket** at `/ws` ([backend/app/ws.py](backend/app/ws.py)) — topic-subscribed pub/sub. Frames: `{"action":"subscribe|unsubscribe|ping","topics":[…]}`. Server pushes envelopes `{type,topic,at,data}`. Topics in use: `inbox:<ws>`, `conv:<id>`, `ticket:<id>`, `teams:<id>`, `notif:<user>`.
- Events already firing: `message.created`, `conversation.updated`, `ticket.created`, `ticket.updated`, `ticket.timeline.appended`, `team.message.created`, `team.typing`, `typing.started`/`stopped`, `notification.created`, `sla.breach_warning`.
- Transport rationale (**WebSocket, not SSE**) in [wire.md](wire.md).

### Backend — background workers

[backend/app/worker.py](backend/app/worker.py) runs three cooperative loops in one asyncio process (advisory-lock friendly so replicas won't double-dispatch):

- **Outbox dispatcher** (every 500 ms) — selects `outbox.status='pending'` with `FOR UPDATE SKIP LOCKED`, routes `send_wa|send_gmail|send_outlook|send_ig|webhook` to kind-specific senders (stub until real adapters ship), exponential backoff (2^n s, cap 1 h), flips to `dead` after 8 attempts.
- **SLA engine** (every 30 s) — finds tickets within 30 min of `sla_due_at`, fans out `sla.breach_warning` on `inbox:<ws>` + `ticket:<id>`, creates a `sla` notification for the assignee, sets `custom_fields.sla_warned_at` so each ticket only fires once.
- **Auto-close** (every 60 s) — `assigned/in_progress` → `waiting_for_customer` after 24 h, `waiting_for_customer` → `resolved` after 72 h, appends timeline entry, publishes `ticket.updated` (spec §3.4.1 "Auto-close worker").

### Backend — notification generators

- `PATCH /tickets/:id` writes a `notification` + `notif:<user>` push when the assignee changes.
- SLA worker writes `sla` notifications (see above).

### Backend — storage, migrations, seed

- **Postgres 16** via asyncpg ([backend/app/db.py](backend/app/db.py)). A compat wrapper translates `?`→`$N` and preserves aiosqlite-style cursor semantics so all routers read uniformly. Type codecs accept ISO-8601 strings for `timestamp/date` and return UUIDs as strings for Pydantic compatibility.
- **schema.sql** ([backend/app/schema.sql](backend/app/schema.sql)) is the authoritative DDL. Now covers `apis`, `integrations`, `api_logs`, `ecommerce_stores`, `outbox`, `agent_runs`, `agent_flow_snapshots`, `password_resets` in addition to Build-1 tables, plus additive `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for workspace AI guardrails (`ai_prompt_append` / `ai_allowed_tools` / `ai_force_default` / `intent_rules` / `langflow_folder_id`), user fields (`oauth_sub` / `langflow_user_id` / `notification_prefs`), agent metadata (`kind` / `langflow_flow_id` / `input_schema` / `output_schema` / `created_by` / `is_active`), and ticket intent columns (`intent_confidence` / `order_ref` / `last_customer_at` / `auto_close_at`). pgvector / `kb_chunks` / vector columns still deferred.
- **Auto-migration + auto-seed** run inside the FastAPI lifespan: wait-for-pg, apply schema, seed if `users` is empty, ensure MinIO bucket exists. Flip `AUTO_MIGRATE=0` / `AUTO_SEED=0` for prod.
- **Seed** ([backend/app/seed.py](backend/app/seed.py)) — demo workspace *The Sleep Company*, 4 users (`demo@workspacex.com / demo`), 5 customers, 4 orders, 5 conversations, 5 tickets spanning `assigned/queued/blocked/resolved`, teams channels + messages, slugged platforms catalog baseline, 4 notifications, `default-orchestrator` agent placeholder.
- **MinIO bootstrap** ([backend/app/s3.py](backend/app/s3.py)) — `ensure_bucket()` runs on startup; creates the bucket + applies public-read policy; non-fatal if MinIO isn't reachable so the API keeps serving non-upload routes.

### Docker / deploy

- [docker-compose.yml](docker-compose.yml) — `db` (pgvector/pg16), `redis`, `minio`, `api`, `worker`, `langflow`, `web`. `api` auto-migrates + auto-seeds; `worker` runs the outbox / SLA / auto-close loops (see above).
- [backend/Dockerfile](backend/Dockerfile) — single image used by both `api` and `worker`; `APP_ROLE` picks the entrypoint.
- [backend/environment.yml](backend/environment.yml) — conda env for dev outside Docker.
- End-to-end smoke passed (containerised): sign-in → /conversations → /tickets → /uploads presign → PUT → GET from MinIO.

### Frontend — wired to the backend

- **AuthPage** ([src/pages/AuthPage.jsx](src/pages/AuthPage.jsx)) posts `/auth/signin` and `/auth/signup`, stores session under `workspacex.auth` (matches the key [src/api.js](src/api.js) reads — fixed a latent `workspacex-auth` vs `workspacex.auth` mismatch that was sending every authed request unauthenticated).
- **Sign-out** ([src/components/UserMenu.jsx](src/components/UserMenu.jsx)) calls `/auth/signout`, closes the WebSocket, clears storage, reloads.
- **Realtime hook** ([src/hooks/useRealtime.js](src/hooks/useRealtime.js)) — one WebSocket per tab, auto-reconnect with backoff, `useRealtimeTopic(topic, handler)` helper. Used by:
  - [src/pages/Conversations.jsx](src/pages/Conversations.jsx) — subscribes to `inbox:<ws>` + `conv:<id>`; optimistic-bubble message sends with `tempId` dedupe against the WS echo (spec §8 "Optimistic UI contract"). File/photo menu now runs the real presign → PUT flow via `uploadFile()` from [src/api.js](src/api.js), swapping the blob URL for the public URL on success.
  - [src/pages/Tickets.jsx](src/pages/Tickets.jsx) — subscribes to `inbox:<ws>` + `ticket:<id>`, refetches detail on `ticket.timeline.appended`. Detail-pane Conversation / Notes / Customer tabs now render from `GET /conversations/:id` + `GET /tickets/:id` notes; the Notes tab posts to `POST /tickets/:id/notes`.
  - [src/pages/Teams.jsx](src/pages/Teams.jsx) — subscribes to `teams:<activeId>`; incoming `team.message.created` appends to the open thread, `team.typing` flashes the indicator for 2.5 s. Self-echoes are deduped by id.
  - [src/hooks/useNotifications.js](src/hooks/useNotifications.js) — subscribes to `notif:<user>`, prepends new notifications live.
- **Upload helper** ([src/api.js](src/api.js)) — `uploadFile(file)` presigns via `POST /uploads` then PUTs the file directly to MinIO (wire.md §5), returning `{url, name, size, mime}` to drop into `messages.attachments`.
- **Settings page** ([src/pages/Settings.jsx](src/pages/Settings.jsx)) — Profile Save wires `PATCH /me`, Workspace Save wires `PATCH /workspace`, Notifications toggles & digest hit `PATCH /me/notification-prefs`, Security Update password hits `POST /me/password`. Each form shows inline success / error state.
- **Agent Builder** ([src/pages/AgentBuilder.jsx](src/pages/AgentBuilder.jsx)) — Save runs `POST /agents` for new agents and `PATCH /agents/:id` for existing; Test calls `POST /agents/:id/test` and renders the orchestrator's output + latency + trace id inline. Model picker now lists `gpt-4.1 / gpt-4.1-mini / gpt-4o / o4-mini` plus the Claude options.
- **Analytics** ([src/pages/Analytics.jsx](src/pages/Analytics.jsx)) — replaces "Charts coming soon" with an inline SVG bar chart backed by `GET /analytics/timeseries` (tickets-per-day / avg-resolution, 14 d).
- **Create-ticket modal** ([src/components/CreateTicketModal.jsx](src/components/CreateTicketModal.jsx)) now POSTs to `/tickets`.
- **Vite proxy** ([vite.config.js](vite.config.js)) forwards `/api` → `http://localhost:8000` and `/ws` → `ws://localhost:8000`.

### Existing design-system work (carried over)

- Light theme tokens, typography, shadows, channel palette — [src/index.css](src/index.css).
- Layout primitives — [src/App.css](src/App.css).
- Shared components — [src/components/Icons.jsx](src/components/Icons.jsx), [src/components/Sidebar.jsx](src/components/Sidebar.jsx), [src/components/TopBar.jsx](src/components/TopBar.jsx).
- Pages: Conversations, Tickets, Customers, Teams, Platforms, Channels/*, Agent Builder, Settings, Analytics, Notifications, Auth.

---

## What's left — backend

Organized by spec §5 priority (Build 2 → 3 → 4). Everything below is absent or stubbed in [backend/app/](backend/app/) today.

### Build 2 — what's still open

**Intent classifier — Stage 2 LLM**
- [x] Stage 2 classifier when Stage 1 is ambiguous (OpenAI structured outputs on `gpt-4.1-mini`). `_classify_stage_two` in [backend/app/routers/tickets.py](backend/app/routers/tickets.py) runs when Stage-1 confidence < 0.6; best-effort fallback to the Stage-1 guess when `OPENAI_API_KEY` / the `openai` SDK aren't available so offline demos still work.
- [x] Pipeline hook that invokes `POST /internal/intent/classify` from the inbound path. `POST /conversations/:id/inbound` writes the customer message, runs the classifier, and applies `attach | reopen | create` before fanning out WS events. Real channel adapters (WhatsApp / Gmail / Shopify) call the same code path when their webhooks land.

**AI reply pipeline — remaining**
- [x] Default orchestrator + typed tools + guardrail skeleton + `agent_runs` ledger — done.
- [x] Loop guard (>8 tool calls force-escalate), per-customer rate limit (20/hr) and consecutive-error limit (3) enforced in the run loop via `@_guard_tool` + `_rate_limit_check` in [backend/app/ai/orchestrator.py](backend/app/ai/orchestrator.py).
- [x] Tool surface now covers `find_customer` / `get_customer` / `get_order` / `list_customer_orders` / `get_shipping_status` / `kb_search` / `similar_tickets` / `create_ticket` / `update_ticket` / `create_child_ticket` / `add_ticket_note` / `send_reply` / `issue_refund` / `escalate_to_human` / `run_library_api`.
- [x] `POST /ai/draft` — composer-side "AI Draft" chip in [src/pages/Conversations.jsx](src/pages/Conversations.jsx) calls the orchestrator with the last inbound message as prompt and drops the output into the textarea so the agent can edit before sending.
- [x] Stream output onto `conv:<id>` so the reply types out live. `POST /conversations/:id/inbound` now kicks off `stream_inbound_reply` which publishes `ai.typing.started`, a sequence of `ai.delta` chunks, and the final `message.created` (flagged `streamFinal`). Conversations.jsx inserts a streaming bubble, appends deltas in place, and swaps it for the persisted row when the final arrives. Token-level deltas ship when `pydantic-ai` exposes a clean streaming surface — today we chunk the final text so the UX still animates.
- [x] Agent selection in the inbound pipeline: `select_inbound_agent(workspace_id)` picks `ai_force_default` → latest `is_active=true` Langflow-published agent → newest `status='published'` builtin → `default-orchestrator` fallback. Wired from `POST /conversations/:id/inbound` so the auto-reply uses the workspace's preferred agent and the saved message row carries `ai_agent_id` for attribution.
- [ ] Install `pydantic-ai-slim[openai]` in prod image (today the fallback handler runs unless the operator pip-installs it + sets `OPENAI_API_KEY`).

**Langflow SSO + publish hook** (spec §3.11.A–C, E) — entirely deferred
- [ ] Langflow container hardened (`LANGFLOW_AUTO_LOGIN=false`, superuser creds, secret key).
- [ ] `GET /flow/launch[?agent=<id>]` admin-token login + per-user folder / user provisioning + `access_token_lf` cookie + 302 to pre-created draft.
- [ ] Reverse-proxy `/flow/**` with WebSocket upgrade + per-folder path guard.
- [ ] `POST /webhooks/langflow/published` HMAC verify + upsert + `agent_flow_snapshots` row + fan-out on `agents:<ws>`.
- [ ] 30 s polling fallback diffing `updated_at` per folder.
- [ ] Flow-as-a-tool ("From Langflow agent" entry in API Library auto-creates an `apis` row).

**Shopify connector + real orders** (spec §6 E-commerce, §3.9.2)
- [x] `POST /ecommerce/stores` connect / delete / sync + `GET /ecommerce/stores` — shell endpoints shipped.
- [x] `POST /webhooks/shopify` with `X-Shopify-Hmac-Sha256` verify — `orders/*` upsert an `orders` row + write an `ecommerce_events` entry; `refunds/*` write a warning event; `products/*` and other topics fall into a generic activity row. Customer linking uses phone / email / shopify id via the shared `resolve_or_create_customer` helper.
- [x] `GET /ecommerce/events?cursor=` activity feed backed by the `ecommerce_events` table (shipped earlier).
- [x] Hourly backfill worker — `_shopify_backfill_tick` in [worker.py](backend/app/worker.py).

**Gmail connector** (spec §6.1)
- [x] `GET /auth/oauth/google/start?mode=connect` → Google OAuth with `gmail.modify` / `gmail.send` and a `platform_credentials` row keyed by email ([routers/oauth.py](backend/app/routers/oauth.py)).
- [x] `POST /webhooks/gmail` accepts Pub/Sub push envelopes, decodes the base64 data, resolves the workspace by `emailAddress`, and runs the shared `ingest_inbound_message` helper.
- [x] Outbound RFC-822 builder → `users.messages.send({raw, threadId})` via the `send_gmail` outbox adapter in [worker.py](backend/app/worker.py); SMTP fallback when the OAuth token is absent.
- [ ] `users.watch` against Cloud Pub/Sub + 6-day cron re-watch.
- [ ] Token refresh path + `status='reauth_required'` notification on `invalid_grant`.

**API Library + API Logs**
- [x] Full CRUD + `POST /internal/apis/:name` + real `api_logs` writes + rolling-24h stats — shipped.
- [x] `GET /api-logs/stream` NDJSON export — wired in [backend/app/routers/channels.py](backend/app/routers/channels.py).
- [x] Real HTTP dispatch inside `_execute_api` — `httpx.AsyncClient` runs the catalog `spec.url` with `{{token}}` template substitution; falls back to echo when no URL is configured.
- [x] Secret redaction on `request`/`response` — `_redact()` strips `authorization|api_key|password|secret|token|x-api-key|access_token` keys before they hit `api_logs`.

**Full Notifications feed** (spec §3.7)
- [x] Ticket-assign generator + SLA generator + `GET/PATCH /me/notification-prefs` — shipped.
- [x] Customer-reply-while-assigned-to-you generator — `POST /conversations/:id/inbound` looks up any linked ticket's assignee and pushes a `reply` notification (with deep-link to the conversation) as the customer message fans out. Also bumps `tickets.last_customer_at` so the auto-close worker resets its idle timer.
- [x] Teams `@mention` generator — `POST /teams/conversations/:id/messages` resolves both structured `{type:"mention"}` blocks and `@Name` / `@First Last` substrings in text and pushes a `mention` notification to each (except the author).
- [x] Teammate-joined + broadcast generators. `POST /workspace/members` creates the invited user + fans a `teammate_joined` notification out to every existing member. `POST /workspace/broadcast` (admin-only) persists a `broadcasts` row and fans `broadcast` notifications to every non-author member; `GET /workspace/broadcasts` backs the "Recent broadcasts" list in Settings → Broadcasts.
- [ ] Email delivery for digest / broadcast (SMTP/SES).

**SLA engine** (spec §3.4, §11)
- [x] Worker loop every 30 s emits `sla.breach_warning` + per-assignee notification once per ticket.
- [x] Per-team/task policy table — `sla_policies(team, task, priority, hours)` resolved by [backend/app/routers/tickets.py](backend/app/routers/tickets.py) `_sla_hours()` (most-specific match wins, falls back to 24 h). CRUD lives at `/workspace/sla-policies` and the Settings → SLA policies tab in [src/pages/Settings.jsx](src/pages/Settings.jsx) wires the admin UI.

**Embedding index** (spec §9)
- [x] `kb_chunks.embedding bytea` + `tickets.subject_embedding bytea` + `embedded_at` columns shipped (additive `ALTER TABLE` in [backend/app/schema.sql](backend/app/schema.sql)).
- [x] Embedding worker — `_embedding_indexer_tick` in [backend/app/worker.py](backend/app/worker.py) runs every 60 s, backfilling 50 rows/sweep through [backend/app/ai/embeddings.py](backend/app/ai/embeddings.py). The helper calls OpenAI `text-embedding-3-small` when `OPENAI_API_KEY` is present and uses a deterministic hashed-bag-of-words sketch otherwise — same `bytes` shape so the indexer / consumers don't branch.
- [x] `tool_kb_search` and `tool_similar_tickets` cosine-rank the embedding column when present, fall back to ILIKE when the column is empty (so a fresh install still returns results).
- [ ] Promote `bytea` → `vector(1536)` + add HNSW index once we depend on pgvector. Today's float32-byte-packed sketch is good enough for ranking 60–100 row candidate sets.

**Outbox dispatcher + retries** (spec §11)
- [x] `outbox` table + dispatcher loop + exponential backoff + `send_reply` tool enqueues rows — shipped.
- [x] `email` adapter — sends via `aiosmtplib` when `SMTP_HOST` is configured; otherwise records the rendered email to `delivery_log` so the operator sees what *would* have been sent. `POST /auth/forgot-password` and `POST /workspace/broadcast` enqueue real `email` rows now (with per-user `notification_prefs.email` opt-in respected for broadcasts).
- [x] `webhook` adapter — `httpx.AsyncClient` POSTs `payload.body` to `payload.url` with optional `payload.headers`. Errors get retried under the existing exponential-backoff policy.
- [x] Channel-specific adapters (`send_wa`, `send_gmail`, `send_outlook`, `send_ig`, `send_slack`, `send_sms`) do real outbound I/O through `httpx` when credentials are present in `platform_credentials`; no-creds runs land in `delivery_log` with an explicit "creds missing" error so the UI is honest. See [worker.py](backend/app/worker.py) `_send_whatsapp` / `_send_gmail` / `_send_outlook` / `_send_instagram` / `_send_slack` / `_send_sms`.
- [x] `delivery_log` table + `GET /workspace/delivery-log` (admin) — surface the dispatcher's history. Settings → Deliveries tab in [src/pages/Settings.jsx](src/pages/Settings.jsx) renders it.

### Build 3 — scale + breadth

**Remaining channel connectors**
- [x] **WhatsApp Business Cloud API** (§6.3) — `GET/POST /webhooks/whatsapp` with the verify-token dance and `X-Hub-Signature-256` HMAC verify, phone-number-id resolution via `platform_credentials(provider='whatsapp')`, outbound `send_wa` adapter calls `graph.facebook.com/<phone_id>/messages`, `statuses` callbacks flip `messages.delivered_at`/`read_at`. Templates + media download still TODO.
- [x] **Instagram DM** (§6.4) — `GET/POST /webhooks/instagram` uses the same Meta verify-token pattern; inbound resolves PSID → `customers.psid`, outbound `send_ig` adapter hits `/<page_id>/messages`.
- [x] **Web widget** (§6.5) — `GET /widget/bootstrap?workspace=<slug>&visitor=<id>` returns `{conversationId, customerId}` (idempotent), `POST /widget/messages` persists inbound + fires the same orchestrator path as the authed inbound endpoint, and `wss:/widget/ws?workspace=&visitor=` streams `message.created` envelopes back to the visitor's browser. `widget_visitors` table tracks one row per browser session and links it to a `customers.kind='visitor'` row + a `conversations.channel='widget'` thread. Wired in [backend/app/routers/widget.py](backend/app/routers/widget.py) — embed script + proactive-prompt triggers still TODO.
- [x] **Outlook / Microsoft Graph** (§6.2) — `POST /webhooks/outlook` honours the `validationToken` handshake + processes `value[]` resourceData into inbound messages; `send_outlook` adapter sends via Graph `/me/sendMail`. Subscription create + renewal cron still TODO.
- [x] **OAuth sign-in** for Google + Outlook (spec §3.1) — `GET /auth/oauth/{google,outlook}/start` + `/callback` in [routers/oauth.py](backend/app/routers/oauth.py) with PKCE code-challenge/verifier stored in the new `oauth_states` table, state TTL reaper in the worker, dev-mode short-circuit when `{PROVIDER}_CLIENT_ID` is unset. AuthPage's "Continue with Google/Outlook" buttons now redirect through the starter and finalize the session on `/#/oauth-landing?token=…` return. JWKS id_token verification + `email_verified` guard still pending.

**Teams internal chat polish** (spec §3.6)
- [x] `teams:<channel_id>` WebSocket subscription wired in [src/pages/Teams.jsx](src/pages/Teams.jsx).
- [x] `GET /teams/presence` + WS `presence.changed` on `workspace:<ws>`; presence reaper worker marking offline after 2 min no-heartbeat (backend/app/worker.py `_presence_tick`). Frontend heartbeats every 30 s and refreshes presence in place.
- [x] `teams_read_cursors` — per-user last-read-at for unread badges. `POST /teams/conversations/:id/read` + `GET /teams/unread` hitched to the active-thread effect in Teams.jsx.
- [x] Block parsing for `@Name` / `@First Last` mentions on the write path — `POST /teams/conversations/:id/messages` resolves mentions and emits `mention` notifications. Frontend send-flow now POSTs the composed blocks (incl. typed mentions) instead of only updating local state.
- [x] `#channel-mentions` parser — `_resolve_channel_mentions` in [backend/app/routers/teams.py](backend/app/routers/teams.py) resolves `{type:"channel_mention",channelId}` blocks *and* `#name` substrings, then publishes `team.channel.mentioned` on the referenced channel's WS topic so anyone with it open sees a "mentioned in #other" pill.

**Analytics + CSAT** (spec §3.10)
- [x] `/analytics/summary` returns real `ticketsThisWeek` + `avgResolution` + `csat` (mean rating × 20) + `aiDeflection` (share of resolved tickets with no human reply).
- [x] `/analytics/timeseries?metric=tickets|resolution|csat|deflection&range=Nd` — all four metrics supported.
- [x] CSAT survey mechanism — `POST /tickets/:id/csat/send` issues a token, `GET /csat/:token` + `POST /csat/:token` are public (unauth) for the customer-facing form. Frontend survey page at [src/pages/PublicCsat.jsx](src/pages/PublicCsat.jsx).
- [x] `analytics_daily` rollup + hourly aggregator worker (`_analytics_rollup_tick`) — folds `tickets_created` / `tickets_resolved` / `avg_resolution_h` / `csat_avg` / `ai_deflection_pct` per workspace-per-day via an upsert so `/analytics/summary` can fall back to pre-aggregated rows at scale. Today's summary still reads live; pre-agg reads arrive alongside the Build-3 scale work.
- [ ] Outbound delivery of the survey link (email / WA template) — requires SMTP/SES + the WA connector.

**Health + incidents** (spec §3.9.6)
- [x] `GET /health/services` returning status/uptime/p95/error-rate per integration (backed by `health_services`).
- [x] `GET /health/services/:id/uptime?window=…` series for uptime bars.
- [x] `GET /incidents`, `POST /incidents/subscribe` (email subscribe persists to `incident_subscribers`; SMTP send still pending).
- [x] `POST /health/probe` — manual re-run endpoint that touches every service row (bumps `last_check_at` + nudges p95). Wired to the Health page's "Re-run checks" button.
- [x] Background health prober worker (`_health_prober_tick` in [backend/app/worker.py](backend/app/worker.py) every 60 s) — auto-updates `health_services` p95/error_rate/status, opens an `incidents` row on degraded/outage transitions (tone=warning/danger) and auto-resolves the incident when the service flips back to `operational`. Thresholds: degraded > 1200 ms p95 or 5% errors; outage > 3500 ms or 20%.
- [x] Real HTTP probes — when an `integrations.config.ping_url` is set for a service, the prober runs an `httpx` HEAD/GET (timeout 6 s) and feeds the live latency + 5xx/transport-error signal into a smoothed p95 (70/30 prior/live blend) and a decaying error_rate (5% on failure, ×0.9 on success). Same threshold + incident logic on the back end of the loop, so wiring a real URL is a one-row config change.

**Counters / cache layer** (spec §9)
- [x] `workspace_counters(workspace_id, kind, value)` — refreshed by `_counter_refresh_tick` every 30 s for `tickets_open`, `conversations_unread`, `notifications_unread`. `GET /workspace/counters` serves the pre-aggregated row so the sidebar doesn't re-count on every page render. Trigger-driven invalidation is an optimisation left for later; at Build-2 volume the 30 s sweep is cheaper than the trigger path's write amplification.
- [ ] Redis hot reads for `me:<id>`, `unread:<id>`, `ticket:<id>`, `conv:<id>:messages:head` with NOTIFY-driven invalidation.
- [ ] Rate-limiter buckets for inbound webhooks.

**Multi-worker realtime** (see [wire.md](wire.md) §"Scaling the broker")
- [ ] Swap the in-process `BROKER` singleton in [backend/app/ws.py](backend/app/ws.py) for Redis Pub/Sub (`SUBSCRIBE topic-*`) so multiple Uvicorn workers can fan out.
- [ ] Read-replica + PgBouncer in docker-compose + session pinning for 500 ms post-write.

**Watch renewer + embedding indexer workers** (spec §11)
- [ ] Daily cron: Gmail `users.watch`, Outlook subscription, Shopify webhook topics.
- [ ] Embedding indexer on write to tickets / kb_chunks.

### Build 4 — polish

- [x] **Billing / Stripe** (spec §3.12) — `GET /billing/{plan,usage,invoices}`, `POST /billing/{checkout,portal}` and `POST /webhooks/stripe` are all wired in [routers/billing.py](backend/app/routers/billing.py) + [routers/webhooks.py](backend/app/routers/webhooks.py). Stripe webhook verifies the `t=…,v1=…` signature and upserts `invoices` / flips `workspaces.plan` on `customer.subscription.*`. Dev-mode checkout flips the plan locally (no real Stripe) so the Settings → Billing upgrade UI round-trips; Stripe kicks in the moment `STRIPE_SECRET_KEY` lands.
- [x] **Google Sheets connector** (spec §3.9.5) — `GET /sheets` (returns `{items, serviceEmail}`), `POST /sheets/validate` (extracts spreadsheet id from a `/spreadsheets/d/<id>` URL + synthesises a name + tab list), `POST /sheets` (persist), `PATCH /sheets/:id` (pause/resume + rename), `DELETE /sheets/:id` (also deletes the corresponding `kb_chunks` rows), `POST /sheets/:id/sync` (re-writes 5 synthetic chunks per tab into `kb_chunks` with source `sheet:<id>` so the orchestrator's `kb_search` can return them). Frontend Sync now / Pause / Resume / Remove are wired in [src/pages/channels/GoogleSheets.jsx](src/pages/channels/GoogleSheets.jsx). Real Sheets API (`spreadsheets.get` + tab pulls) lands with the OAuth wiring; the row writer + kb_chunks invalidation behaves identically when it does.
- [x] **Slack escalations** (spec §6.6) — `POST /webhooks/slack/events` (signing-secret verify + URL verification handshake), `POST /webhooks/slack/interactions` (Block Kit callback, `claim_ticket` action flips ticket status), outbound `send_slack` adapter posts Block Kit cards via `chat.postMessage`. Bot-install OAuth still TODO.
- [x] **Voice / call recordings** (spec §6.7) — `POST /webhooks/voice` accepts Twilio/Exotel JSON or form-encoded payloads, resolves the caller to a `customers` row, stashes the recording URL on `conversations.recordings[]`, and persists a `system` message with the duration. Real MinIO download + Whisper transcript still open.
- [x] `GET /customers/:id/recordings` + `GET /channels/for-customer/:id` shipped in [backend/app/routers/customers.py](backend/app/routers/customers.py). Real recording ingestion waits on the voice adapter.
- [x] **2FA** — RFC-6238 TOTP via `_b32_secret` + `_verify_totp` in [backend/app/routers/me.py](backend/app/routers/me.py). Endpoints: `GET /me/2fa` (status), `POST /me/2fa/setup` (mints a secret + `otpauth://` URL, stages it under `users.totp_secret_pending`), `POST /me/2fa/verify` (promotes to `totp_secret` after a valid 6-digit code), `POST /me/2fa/disable` (also requires a code so a stolen session can't silently disable the second factor). Settings → Security renders the setup flow inline with the secret + manual OTPauth URL fallback. Encrypting the secret with `pgp_sym_encrypt` is the next pass.
- [x] **Forgot-password** flow — `POST /auth/forgot-password` issues a single-use hashed token (1 h TTL, stored in `password_resets`), `POST /auth/reset-password` consumes it + rehashes + drops every outstanding `auth_sessions` row so other tabs are forced to re-auth. In dev (`DEV_RESET_TOKEN=1`, default on) the response echoes the raw token so the AuthPage "Forgot password?" link can complete the flow without SMTP. SMTP/SES delivery still open.
- [x] **Account linking / unlinking** — `DELETE /me/oauth/:provider` clears `oauth_sub` + flips `users.provider` back to `password`. Blocked with 400 when the user has no password hash and is currently on that provider, so they can't lock themselves out.

### Cross-cutting hygiene still open

- [ ] **Schema gap vs. spec §7** — remaining: `pgvector` + `btree_gin` extensions, `subject_embedding`/HNSW, `pgp_sym_encrypt` wrap on `platform_credentials.secrets`. (`platform_credentials`, `oauth_states`, `invoices`, `kb_chunks`, `teams_read_cursors`, `idempotency_keys`, `csat_tokens`, `outbox`, `agent_runs`, `agent_flow_snapshots`, `password_resets`, workspace/user/agent Langflow columns, intent-classifier columns all present now.)
- [x] **RBAC enforcement** — `AgentOrAdmin` dep applied across `POST|PATCH|DELETE` routes in [tickets.py](backend/app/routers/tickets.py) + [conversations.py](backend/app/routers/conversations.py). `AdminOnly` still guards `PATCH /workspace`. Viewers can read but not mutate.
- [x] **Idempotency keys** — `Idempotency-Key` header honoured on `POST /tickets` and `POST /conversations/:id/messages` via [backend/app/idempotency.py](backend/app/idempotency.py). Falls back to `body.tempId` for messages so the optimistic-UI contract is covered by default.
- [ ] **RLS** — workspace scoping is app-level `WHERE workspace_id=$ws`; consider Postgres RLS before multi-tenant load.
- [ ] **Tracing** — `api_logs.trace_id` fabricated per-row; wire OpenTelemetry so the same `trace_id` spans webhook → DB write → WS push.

---

## What's left — frontend (still stubby)

- **Conversations**: AI Draft chip + a **Simulate inbound** chip (posts to `/conversations/:id/inbound` with a prompt for the inbound text, so the intent classifier + WS fan-out round-trips live). Private Notes panel's "Add Note" button now POSTs to `/conversations/:id/notes` with an optimistic bubble. Email-thread view renders for `email`/`gmail`/`outlook`/`Mails` channels — inbound mail gets a header + body card with sender email/phone, outbound agent mail mirrors it on the right. AI auto-replies animate in place as `ai.delta` events land — streaming bubble shows a blinking cursor, then gets replaced by the persisted row when `streamFinal` arrives.
- **Tickets**: Merge / split / attach-message modals replaced the old `prompt()` fallback; sort icon decorative, pagination slices a fully-fetched array.
- **Channels**: API Library / API Logs / E-Commerce / Google Sheets / Health pages wired to their endpoints. ApiLogs now reads real `/api-logs/:id` detail (previously crashed on `.timestamp.toLocaleString()`).
- **Platforms**: `GET /platforms?include=all` now backs the grid so every catalog entry renders; Connect for OAuth channels (WhatsApp / Gmail / Outlook / Instagram) surfaces a Manage modal explaining the OAuth round-trip is stubbed, Connect for others flips state directly.
- Mobile / responsive, dark mode audit, keyboard shortcuts, loading/empty/error states, tests — unchanged.

---

## How to run the whole stack

```bash
# Terminal 1 — everything in Docker (datastores + api + worker)
docker compose up -d db minio redis api worker
docker logs -f workspacex-api-1        # watch the schema apply + seed

# Terminal 2 — frontend
npm install
npm run dev                             # http://localhost:5173
```

Sign in with `demo@workspacex.com / demo`. Open two tabs to see realtime — a reply sent from one tab appears in the other tab's chat pane without a refetch, and the underlying ticket's timeline picks up the `Agent X replied on <channel>` entry.

Alternative: run just the datastores in Docker and the API on the host (pip or conda) for fast edit-reload — see [backend/README.md](backend/README.md).
