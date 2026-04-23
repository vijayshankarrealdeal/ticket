# Progress

Last updated: 2026-04-23

Status against [BACKEND_SPEC.md](BACKEND_SPEC.md). Frontend progress lives in this file too (it grew out of the original SPA log). Wiring contract between frontend and backend is in [wire.md](wire.md).

---

## What's done

### Backend — Build 1 spine (Auth ↔ Conversation ↔ Ticket)

FastAPI service at [backend/](backend/). **Stack: Postgres 16 + asyncpg + MinIO + in-process WebSocket broker.** `docker compose up -d db minio redis api worker` brings everything online; on first boot the api container auto-applies [backend/app/schema.sql](backend/app/schema.sql) and seeds a demo workspace, and ensures the MinIO bucket exists.

- **Auth** ([backend/app/routers/auth.py](backend/app/routers/auth.py)) — Argon2id password auth, 32-byte bearer tokens stored as `sha256(token)` in `auth_sessions`, `GET /auth/me`, `POST /auth/signout`, `wx_session` cookie mirror. OAuth endpoints short-circuit for Build 1; recipes stand ready in spec §3.1.
- **Me + workspace** ([backend/app/routers/me.py](backend/app/routers/me.py)) — `GET/PATCH /me`, `GET /me/unread-counts`, `GET /workspace`, `GET /workspace/members`.
- **Customers** ([backend/app/routers/customers.py](backend/app/routers/customers.py)) — list, detail (with `recentOrders` + `tickets[]`), search, create, patch.
- **Conversations + messages** ([backend/app/routers/conversations.py](backend/app/routers/conversations.py))
  - `GET /conversations` (channel/tab/q filters), `GET /conversations/:id` (messages + customer + orders + tickets + notes all nested in the shape [src/pages/Conversations.jsx](src/pages/Conversations.jsx) renders).
  - `POST /conversations/:id/messages` persists the agent reply, fans out `message.created` on `conv:<id>` and `conversation.updated` on `inbox:<ws>`, appends `Agent X replied on <channel>` to every linked ticket's timeline.
  - `POST /conversations/:id/notes`, `POST /conversations/:id/typing`, `POST /messages/:id/read`, `GET /conversations/:id/tickets`.
- **Tickets** ([backend/app/routers/tickets.py](backend/app/routers/tickets.py)) — list with filter/sort/q, detail with nested `customerDetails/timeline/notes/order/customFields/pendingChild`, `POST /tickets` (auto-creates a guest customer when needed), `PATCH /tickets/:id` (writes timeline entries on every state change), `POST /tickets/:id/notes`, `POST/PATCH /tickets/:id/child`, `POST /tickets/:id/resolve`, `POST /tickets/similar` (ILIKE match; pgvector HNSW in Build 2).
- **Teams internal chat** ([backend/app/routers/teams.py](backend/app/routers/teams.py)) — members, channels, DMs, messages, reaction toggle, typing broadcasts.
- **Notifications** ([backend/app/routers/notifications.py](backend/app/routers/notifications.py)) — list/mark-read/read-all/dismiss/clear plus a helper any other route can call to push a typed notification to a user.
- **Global search** ([backend/app/routers/search.py](backend/app/routers/search.py)) — `GET /search?q=&limit=` grouped by conversations/tickets/contacts (case-insensitive ILIKE).
- **Platforms + stats** ([backend/app/routers/platforms.py](backend/app/routers/platforms.py)) — slug-keyed catalog merged with per-workspace state; list, stats, connect, disconnect (ON CONFLICT upsert).
- **Analytics** ([backend/app/routers/analytics.py](backend/app/routers/analytics.py)) — `/summary`, `/timeseries`.
- **Agents** ([backend/app/routers/agents.py](backend/app/routers/agents.py)) — CRUD + mock `POST /agents/:id/test` responder.
- **Channels stubs** ([backend/app/routers/channels.py](backend/app/routers/channels.py)) — `ecommerce/*`, `apis`, `integrations`, `api-logs[/stats]`, `sheets`, `health/services`, `incidents` return structured-but-empty payloads so every Channels page loads.
- **Uploads → MinIO** ([backend/app/routers/uploads.py](backend/app/routers/uploads.py) + [backend/app/s3.py](backend/app/s3.py)) — `POST /uploads` returns a real presigned S3-v4 URL against MinIO. Smoke-tested PUT → 200 → GET → 200. Rewrites the host to `S3_PUBLIC_URL` so a browser on `:3000` can reach the same object the api uses via `minio:9000`.

### Backend — realtime

- **WebSocket** at `/ws` ([backend/app/ws.py](backend/app/ws.py)) — topic-subscribed pub/sub. Frames: `{"action":"subscribe|unsubscribe|ping","topics":[…]}`. Server pushes envelopes `{type,topic,at,data}`. Topics in use: `inbox:<ws>`, `conv:<id>`, `ticket:<id>`, `teams:<id>`, `notif:<user>`.
- Events already firing: `message.created`, `conversation.updated`, `ticket.created`, `ticket.updated`, `ticket.timeline.appended`, `team.message.created`, `team.typing`, `typing.started`/`stopped`, `notification.created`.
- Transport rationale (**WebSocket, not SSE**) in [wire.md](wire.md).

### Backend — storage, migrations, seed

- **Postgres 16** via asyncpg ([backend/app/db.py](backend/app/db.py)). A compat wrapper translates `?`→`$N` and preserves aiosqlite-style cursor semantics so all routers read uniformly. Type codecs accept ISO-8601 strings for `timestamp/date` and return UUIDs as strings for Pydantic compatibility.
- **schema.sql** ([backend/app/schema.sql](backend/app/schema.sql)) is the authoritative DDL (subset of spec §7 — no pgvector, kb_chunks, or vector columns yet). Every statement is `IF NOT EXISTS` so boots are idempotent.
- **Auto-migration + auto-seed** run inside the FastAPI lifespan: wait-for-pg, apply schema, seed if `users` is empty, ensure MinIO bucket exists. Flip `AUTO_MIGRATE=0` / `AUTO_SEED=0` for prod.
- **Seed** ([backend/app/seed.py](backend/app/seed.py)) — demo workspace *The Sleep Company*, 4 users (`demo@workspacex.com / demo`), 5 customers, 4 orders, 5 conversations, 5 tickets spanning `assigned/queued/blocked/resolved`, teams channels + messages, slugged platforms catalog baseline, 4 notifications, `default-orchestrator` agent placeholder.
- **MinIO bootstrap** ([backend/app/s3.py](backend/app/s3.py)) — `ensure_bucket()` runs on startup; creates the bucket + applies public-read policy; non-fatal if MinIO isn't reachable so the API keeps serving non-upload routes.

### Docker / deploy

- [docker-compose.yml](docker-compose.yml) — `db` (pgvector/pg16), `redis`, `minio`, `api`, `worker`, `langflow`, `web`. `api` auto-migrates + auto-seeds; `worker` is a heartbeat stub ([backend/app/worker.py](backend/app/worker.py)) until Build 2 jobs land.
- [backend/Dockerfile](backend/Dockerfile) — single image used by both `api` and `worker`; `APP_ROLE` picks the entrypoint.
- [backend/environment.yml](backend/environment.yml) — conda env for dev outside Docker.
- End-to-end smoke passed (containerised): sign-in → /conversations → /tickets → /uploads presign → PUT → GET from MinIO.

### Frontend — wired to the backend

- **AuthPage** ([src/pages/AuthPage.jsx](src/pages/AuthPage.jsx)) posts `/auth/signin` and `/auth/signup`, stores session under `workspacex.auth` (matches the key [src/api.js](src/api.js) reads — fixed a latent `workspacex-auth` vs `workspacex.auth` mismatch that was sending every authed request unauthenticated).
- **Sign-out** ([src/components/UserMenu.jsx](src/components/UserMenu.jsx)) calls `/auth/signout`, closes the WebSocket, clears storage, reloads.
- **Realtime hook** ([src/hooks/useRealtime.js](src/hooks/useRealtime.js)) — one WebSocket per tab, auto-reconnect with backoff, `useRealtimeTopic(topic, handler)` helper. Used by:
  - [src/pages/Conversations.jsx](src/pages/Conversations.jsx) — subscribes to `inbox:<ws>` + `conv:<id>`; optimistic-bubble message sends with `tempId` dedupe against the WS echo (spec §8 "Optimistic UI contract").
  - [src/pages/Tickets.jsx](src/pages/Tickets.jsx) — subscribes to `inbox:<ws>` + `ticket:<id>`, refetches detail on `ticket.timeline.appended`.
  - [src/hooks/useNotifications.js](src/hooks/useNotifications.js) — subscribes to `notif:<user>`, prepends new notifications live.
- **Create-ticket modal** ([src/components/CreateTicketModal.jsx](src/components/CreateTicketModal.jsx)) now POSTs to `/tickets`.
- **Vite proxy** ([vite.config.js](vite.config.js)) forwards `/api` → `http://localhost:8000` and `/ws` → `ws://localhost:8000`.

### Existing design-system work (carried over)

- Light theme tokens, typography, shadows, channel palette — [src/index.css](src/index.css).
- Layout primitives — [src/App.css](src/App.css).
- Shared components — [src/components/Icons.jsx](src/components/Icons.jsx), [src/components/Sidebar.jsx](src/components/Sidebar.jsx), [src/components/TopBar.jsx](src/components/TopBar.jsx).
- Pages: Conversations, Tickets, Customers, Teams, Platforms, Channels/*, Agent Builder, Settings, Analytics, Notifications, Auth.

---

## What's left

### Backend — deferred from Build 1 to Build 2/3

- **OAuth** (Google + Outlook) start URLs stub; full recipe in spec §3.1. Needs Redis for state/nonce.
- **Channel connectors** — WhatsApp / Gmail / Outlook / Instagram / web-widget (spec §6). Build 1 doesn't ingest anything real yet.
- **Intent classifier + auto-ticket** (spec §3.4.1) — not wired. Seed manually links one ticket per conversation.
- **AI reply pipeline + default orchestrator** (spec §3.11.H + §10) — agent row exists but `POST /agents/:id/test` returns a canned string. Wire `pydantic-ai` + `OPENAI_API_KEY` to go live.
- **Langflow SSO + publish hook** (spec §3.11.A–C).
- **Shopify connector + real orders** (spec §6 E-commerce).
- **SLA engine** (spec §3.4 + §11) — due-at is computed on create but no worker fires `sla.breach_warning`.
- **Embedding index** (spec §9) — `similar_tickets` is ILIKE today. Needs pgvector HNSW column + `text-embedding-3-small`.
- **Counters / cache layer** (spec §9) — inbox/ticket counts computed at read-time. Add `workspace_counters` + Redis hot reads under load.
- **Outbox dispatcher + retries** (spec §11) — every outbound channel send should go through `outbox` for idempotency.
- **Notifications generators** — ticket assign/reply/SLA events don't auto-create notification rows yet; only seed fixture has them.
- **Analytics aggregator + charts** (spec §3.10) — `/summary` returns live `tickets` count but `avgResolution / csat / aiDeflection` are placeholders.
- **2FA / forgot-password delivery**, **billing**.
- **Multi-worker realtime** — the in-process broker fans out within one Uvicorn process. Swap for Redis Pub/Sub per [wire.md](wire.md) §"Scaling the broker".

### Frontend — still stubby

- **Conversations**: AI Draft chip, email-thread view, full photo/file upload flow (UI exists, `POST /uploads` returns a real presign, but the PUT step in the React layer isn't wired yet).
- **Tickets**: `Conversation | Notes | Customer` tabs render placeholders — data is available on the server, just not hooked in. Sort icon decorative. Pagination slices a fully-fetched array.
- **Teams**: no `teams:<id>` WebSocket subscription yet (channel list refreshes via refetch).
- **Agent Builder**: Save/Test buttons don't persist.
- **Settings**: Save buttons don't hit `PATCH /me` / `PATCH /workspace`.
- **Integrations / API Library / API Logs / Sheets / Health**: read-only shells; Connect/Configure are decorative.
- **Analytics charts**: "Charts coming soon" — wire recharts to `/analytics/timeseries`.
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
