# Progress

Last updated: 2026-04-23 — Second round of wiring: inbound message pipeline (`POST /conversations/:id/inbound` → intent classifier → attach/reopen/create), `POST /ai/draft` for composer-side AI suggestions, idempotency keys on tickets + messages, RBAC (`AgentOrAdmin` / `AdminOnly`) enforced across mutation routes, public CSAT survey flow (`/csat/:token`), merge/split/attach-message modals replacing `prompt()`, Platforms page wired to `include=all` catalog with Manage/OAuth-stub modal, ApiLogs page fixed against real `/api-logs/:id` detail shape.

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
- **Teams internal chat** ([backend/app/routers/teams.py](backend/app/routers/teams.py)) — members, channels, DMs, messages, reaction toggle, typing broadcasts.
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
- [ ] Stage 2 classifier when Stage 1 is ambiguous (OpenAI structured outputs on `gpt-4.1-mini`). Stage 1 keyword/order-ref router + `POST /internal/intent/classify` + split/merge/attach-message endpoints + auto-close worker all shipped.
- [x] Pipeline hook that invokes `POST /internal/intent/classify` from the inbound path. `POST /conversations/:id/inbound` writes the customer message, runs the classifier, and applies `attach | reopen | create` before fanning out WS events. Real channel adapters (WhatsApp / Gmail / Shopify) call the same code path when their webhooks land.

**AI reply pipeline — remaining**
- [x] Default orchestrator + typed tools + guardrail skeleton + `agent_runs` ledger — done.
- [x] Loop guard (>8 tool calls force-escalate), per-customer rate limit (20/hr) and consecutive-error limit (3) enforced in the run loop via `@_guard_tool` + `_rate_limit_check` in [backend/app/ai/orchestrator.py](backend/app/ai/orchestrator.py).
- [x] Tool surface now covers `find_customer` / `get_customer` / `get_order` / `list_customer_orders` / `get_shipping_status` / `kb_search` / `similar_tickets` / `create_ticket` / `update_ticket` / `create_child_ticket` / `add_ticket_note` / `send_reply` / `issue_refund` / `escalate_to_human` / `run_library_api`.
- [x] `POST /ai/draft` — composer-side "AI Draft" chip in [src/pages/Conversations.jsx](src/pages/Conversations.jsx) calls the orchestrator with the last inbound message as prompt and drops the output into the textarea so the agent can edit before sending.
- [ ] Stream output via SSE onto `conv:<id>` so the reply types out live. `POST /agents/:id/run` already streams SSE, but the inbound pipeline still fans out a single `message.created` event.
- [ ] Agent selection in the inbound pipeline: `ai_force_default` → latest published Langflow → default-orchestrator fallback (selection harness not yet wired; `/agents/:id/test` always runs the default).
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
- [ ] Webhook handlers for `orders/*`, `products/*`, `refunds/*` + customer linking by shopify id / email / phone.
- [ ] `GET /ecommerce/events?cursor=` activity feed (currently returns `[]`).
- [ ] Hourly backfill worker.

**Gmail connector** (spec §6.1) — entirely deferred
- [ ] `POST /platforms/gmail/connect` → Google OAuth with `gmail.modify` / `gmail.send` + `platform_credentials.secret_enc`.
- [ ] `users.watch` against Cloud Pub/Sub + 6-day cron re-watch.
- [ ] `POST /webhooks/gmail` OIDC-verified inbound → dedupe by `Message-ID`.
- [ ] Outbound RFC-822 builder → `users.messages.send({raw, threadId})` via outbox.
- [ ] Token refresh path + `status='reauth_required'` notification on `invalid_grant`.

**API Library + API Logs**
- [x] Full CRUD + `POST /internal/apis/:name` + real `api_logs` writes + rolling-24h stats — shipped.
- [ ] `GET /api-logs/stream` NDJSON export.
- [ ] Real HTTP dispatch inside `_execute_api` (today it echoes the spec and logs a synthetic 200).
- [ ] Secret redaction on `request`/`response` when real outbound calls land.

**Full Notifications feed** (spec §3.7)
- [x] Ticket-assign generator + SLA generator + `GET/PATCH /me/notification-prefs` — shipped.
- [ ] Customer-reply-while-assigned-to-you generator (waits on inbound channel webhooks).
- [ ] Teammate-joined + broadcast generators.
- [ ] Email delivery for digest / broadcast (SMTP/SES).

**SLA engine** (spec §3.4, §11)
- [x] Worker loop every 30 s emits `sla.breach_warning` + per-assignee notification once per ticket.
- [ ] Per-team/task policy table (currently `sla_due_at = created_at + 24h` hard-coded in `POST /tickets`).

**Embedding index** (spec §9) — entirely deferred
- [ ] `CREATE EXTENSION vector` + `tickets.subject_embedding vector(1536)` + HNSW index.
- [ ] `kb_chunks` table with HNSW.
- [ ] Embedding worker on ticket-create / subject-edit → `openai.embeddings.create(model='text-embedding-3-small')`.
- [ ] Swap `POST /tickets/similar` ILIKE for HNSW cosine search.

**Outbox dispatcher + retries** (spec §11)
- [x] `outbox` table + dispatcher loop + exponential backoff + `send_reply` tool enqueues rows — shipped.
- [ ] Real adapter implementations per kind (`send_wa`, `send_gmail`, `send_outlook`, `send_ig`, `webhook`) — currently log + mark `sent` without doing outbound I/O.

### Build 3 — scale + breadth

**Remaining channel connectors**
- [ ] **WhatsApp Business Cloud API** (§6.3) — phone-number-id config, HMAC-verified `POST /webhooks/whatsapp`, media fetch to MinIO, `message_status` → delivered/read ticks, 24 h session window tracking (`conversations.last_inbound_at`), approved-template sends outside the window.
- [ ] **Instagram DM** (§6.4) — Meta Page webhook with `messaging` field, PSID → `customers` mapping, 7-day window.
- [ ] **Web widget** (§6.5) — `wss://api/widget?workspace=&visitor=` endpoint, `customers.kind='visitor'` bootstrap, proactive-prompt triggers, embed script served from CDN path.
- [ ] **Outlook / Microsoft Graph** (§6.2) — subscription create + 48 h `PATCH` renewal, delta query on `me/mailFolders('Inbox')/messages`, reply via `/me/messages/{id}/reply`.
- [ ] **OAuth sign-in** for Google + Outlook (spec §3.1) — the same OAuth infra feeds both sign-in and the Gmail/Outlook platform connects. Needs Redis for `oauth:<provider>:<state>` with PKCE verifier + nonce, JWKS validation of id_token, `email_verified` guard, `users.oauth_sub`/`avatar_url` columns.

**Teams internal chat polish** (spec §3.6)
- [x] `teams:<channel_id>` WebSocket subscription wired in [src/pages/Teams.jsx](src/pages/Teams.jsx).
- [ ] `GET /presence` + WS `presence.changed`; presence reaper worker marking offline after 2 min no-heartbeat.
- [ ] `teams_read_cursors` — per-user last-read-at for unread badges.
- [ ] Block parsing for `@mentions` + `#channel-mentions` on write path.

**Analytics + CSAT** (spec §3.10)
- [x] `/analytics/summary` returns real `ticketsThisWeek` + `avgResolution` + `csat` (mean rating × 20) + `aiDeflection` (share of resolved tickets with no human reply).
- [x] `/analytics/timeseries?metric=tickets|resolution|csat|deflection&range=Nd` — all four metrics supported.
- [x] CSAT survey mechanism — `POST /tickets/:id/csat/send` issues a token, `GET /csat/:token` + `POST /csat/:token` are public (unauth) for the customer-facing form. Frontend survey page at [src/pages/PublicCsat.jsx](src/pages/PublicCsat.jsx).
- [ ] `analytics_daily` rollup + nightly aggregator worker (live queries today — fine at low volume, will need pre-agg at scale).
- [ ] Outbound delivery of the survey link (email / WA template) — requires SMTP/SES + the WA connector.

**Health + incidents** (spec §3.9.6)
- [ ] `GET /health/services` returning real status/uptime/p95/error-rate per integration.
- [ ] `GET /health/services/:id/uptime?window=…` series for uptime bars.
- [ ] `GET /incidents`, `POST /incidents/subscribe` (email alerts).
- [ ] Health prober worker pinging each integration every 30 s, updating `health_services`, opening `incidents` on threshold breach.

**Counters / cache layer** (spec §9)
- [ ] `workspace_counters(workspace_id, kind, value)` updated via trigger on `conversations`, `tickets`, `notifications`.
- [ ] Redis hot reads for `me:<id>`, `unread:<id>`, `ticket:<id>`, `conv:<id>:messages:head` with NOTIFY-driven invalidation.
- [ ] Rate-limiter buckets for inbound webhooks.

**Multi-worker realtime** (see [wire.md](wire.md) §"Scaling the broker")
- [ ] Swap the in-process `BROKER` singleton in [backend/app/ws.py](backend/app/ws.py) for Redis Pub/Sub (`SUBSCRIBE topic-*`) so multiple Uvicorn workers can fan out.
- [ ] Read-replica + PgBouncer in docker-compose + session pinning for 500 ms post-write.

**Watch renewer + embedding indexer workers** (spec §11)
- [ ] Daily cron: Gmail `users.watch`, Outlook subscription, Shopify webhook topics.
- [ ] Embedding indexer on write to tickets / kb_chunks.

### Build 4 — polish

- [ ] **Billing / Stripe** (spec §3.12) — `GET /billing/plan|usage|invoices`, `POST /billing/checkout`, Stripe webhook for plan changes.
- [ ] **Google Sheets connector** (spec §3.9.5) — service-account provisioning, `POST /sheets/validate` URL resolver, `POST /sheets`, pause/resume via `PATCH`, `DELETE`, 5-min sync worker that writes sheet rows into `kb_chunks`.
- [ ] **Slack escalations** (spec §6.6) — bot-token install, `chat.postMessage` Block Kit card on ticket escalate, `POST /webhooks/slack/interactions` with signing-secret verify.
- [ ] **Voice / call recordings** (spec §6.7) — Exotel/Twilio/Plivo webhook → download → MinIO → `conversations.recordings[]`; optional Whisper transcript.
- [x] `GET /customers/:id/recordings` + `GET /channels/for-customer/:id` shipped in [backend/app/routers/customers.py](backend/app/routers/customers.py). Real recording ingestion waits on the voice adapter.
- [ ] **2FA** — `otplib` TOTP, `POST /me/2fa/enable|verify`, `users.secret_enc` via `pgp_sym_encrypt`.
- [ ] **Forgot-password email delivery** — `password_resets` table exists in spec; route + SMTP/SES wiring not done.
- [ ] **Account linking / unlinking** — `DELETE /me/oauth/:provider` (blocked if `password_hash IS NULL`).

### Cross-cutting hygiene still open

- [ ] **Schema gap vs. spec §7** — remaining: `pgvector` + `btree_gin` extensions, `subject_embedding`/HNSW, `platform_credentials.secret_enc`. (`kb_chunks`, `teams_read_cursors`, `idempotency_keys`, `csat_tokens`, `outbox`, `agent_runs`, `agent_flow_snapshots`, `password_resets`, workspace/user/agent Langflow columns, intent-classifier columns all present now.)
- [x] **RBAC enforcement** — `AgentOrAdmin` dep applied across `POST|PATCH|DELETE` routes in [tickets.py](backend/app/routers/tickets.py) + [conversations.py](backend/app/routers/conversations.py). `AdminOnly` still guards `PATCH /workspace`. Viewers can read but not mutate.
- [x] **Idempotency keys** — `Idempotency-Key` header honoured on `POST /tickets` and `POST /conversations/:id/messages` via [backend/app/idempotency.py](backend/app/idempotency.py). Falls back to `body.tempId` for messages so the optimistic-UI contract is covered by default.
- [ ] **RLS** — workspace scoping is app-level `WHERE workspace_id=$ws`; consider Postgres RLS before multi-tenant load.
- [ ] **Tracing** — `api_logs.trace_id` fabricated per-row; wire OpenTelemetry so the same `trace_id` spans webhook → DB write → WS push.

---

## What's left — frontend (still stubby)

- **Conversations**: AI Draft chip now calls `/ai/draft` and populates the textarea. Email-thread view + inbound simulator button still missing.
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
