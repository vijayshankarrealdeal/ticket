# wire.md — How the frontend is wired to the backend

> Companion to [BACKEND_SPEC.md](BACKEND_SPEC.md) and [progress.md](progress.md). This file documents the exact contracts the SPA and the FastAPI service use today — enough for either side to change independently without silently breaking the other.

---

## 1. Topology

```
┌────────────────────┐       HTTP + WS        ┌────────────────────┐     TCP      ┌─────────────┐
│  React SPA (Vite)  │ ─────────────────────► │  FastAPI + Uvicorn │ ────────────►│ Postgres 16 │
│    localhost:5173  │                        │    localhost:8000  │  asyncpg     │ (pgvector)  │
│                    │ ◄─────────────────────  │                    │              └─────────────┘
│  /api → proxy      │  JSON                  │  asyncpg pool      │     S3       ┌─────────────┐
│  /ws  → proxy      │  WebSocket envelopes   │  in-proc broker    │─────────────►│   MinIO     │
└────────────────────┘                        └────────────────────┘  boto3       │ (S3 clone)  │
                                                                                   └─────────────┘
                                   presigned PUT ↓         public GET ↑
                                                ╲           ╱
                                                 ╲         ╱
                                           browser uploads directly
```

- In dev, Vite proxies `/api` → `:8000` and `/ws` → `ws://localhost:8000` (see [vite.config.js](vite.config.js)). The SPA only ever talks to its own origin.
- In prod, the same path mapping is done by nginx (spec §13). Override `VITE_API_URL` at build time if your reverse proxy lives elsewhere.
- Datastores + api all run in Docker via [docker-compose.yml](docker-compose.yml). On first boot the api container: (1) waits for Postgres, (2) applies [backend/app/schema.sql](backend/app/schema.sql) with `IF NOT EXISTS`, (3) runs the demo seed if `users` is empty, (4) ensures the MinIO bucket exists. Flip `AUTO_MIGRATE=0` / `AUTO_SEED=0` for prod environments where migrations run out-of-band.

---

## 2. HTTP contract

### Base URL + auth header

Client: [src/api.js](src/api.js). All requests go through a single `request()` helper:

- Base URL: `import.meta.env.VITE_API_URL || '/api'`.
- Auth: if `localStorage['workspacex.auth']` has a `token` (or `accessToken`), the helper adds `Authorization: Bearer <token>`. If it doesn't, requests go out anonymous — the server will 401 anything except `/auth/*` and `/healthz`.
- Content: `Accept: application/json`; `Content-Type: application/json` on writes.

> ⚠️ The localStorage key must be `workspacex.auth` (with a dot). Earlier code used `workspacex-auth` (with a hyphen); the SPA saved under one key and read under another, so every authed request silently went out unauthenticated. Fixed in [src/pages/AuthPage.jsx](src/pages/AuthPage.jsx).

### Auth flow

```
┌──────────┐  POST /auth/signin           ┌─────────────┐
│ AuthPage ├─── {email, password} ───────►│  FastAPI    │
│          │                              │  (Argon2id) │
│          │◄── {user, token, accessToken}┤             │
└──────────┘                              └─────────────┘
       │
       │ saveAuth({ user, token })        localStorage['workspacex.auth']
       │
       ▼
   api.js reads token  ───►  Authorization: Bearer <token>
   useRealtime reads token ──►  ws://…/ws?token=<token>
```

Session lifetime: 30 days sliding. The server also sets an httpOnly `wx_session` cookie — equivalent to the bearer token but safer against XSS. Clients may ignore it and keep using the header; the server accepts either.

Sign-out: `clearAuth()` removes the localStorage entry and fires `POST /auth/signout` best-effort, which deletes the `auth_sessions` row so the token can't be reused. [src/components/UserMenu.jsx](src/components/UserMenu.jsx) also calls `closeRealtime()` to drop the socket.

### Endpoint-by-endpoint wiring

| SPA consumer | Method + path | Server handler |
|---|---|---|
| [AuthPage.jsx](src/pages/AuthPage.jsx) | `POST /auth/signin`, `/auth/signup` | [auth.py](backend/app/routers/auth.py) |
| [UserMenu.jsx](src/components/UserMenu.jsx), [api.js](src/api.js) | `POST /auth/signout`, `GET /auth/me` | [auth.py](backend/app/routers/auth.py) |
| [Conversations.jsx](src/pages/Conversations.jsx) | `GET /conversations`, `GET /conversations/:id`, `POST /conversations/:id/messages` | [conversations.py](backend/app/routers/conversations.py) |
| [Tickets.jsx](src/pages/Tickets.jsx), [CreateTicketModal.jsx](src/components/CreateTicketModal.jsx) | `GET /tickets`, `GET /tickets/:id`, `POST /tickets`, `PATCH /tickets/:id`, `POST /tickets/similar` | [tickets.py](backend/app/routers/tickets.py) |
| [Customers.jsx](src/pages/Customers.jsx), [CreateTicketModal.jsx](src/components/CreateTicketModal.jsx) | `GET /customers`, `GET /customers/:id`, `GET /customers/search` | [customers.py](backend/app/routers/customers.py) |
| [Teams.jsx](src/pages/Teams.jsx), [teamsData.js](src/data/teamsData.js) | `GET /teams/*`, `POST /teams/*`, `/auth/me` | [teams.py](backend/app/routers/teams.py), [auth.py](backend/app/routers/auth.py) |
| [useNotifications.js](src/hooks/useNotifications.js) | `GET /notifications`, `POST /notifications/:id/read`, … | [notifications.py](backend/app/routers/notifications.py) |
| [GlobalSearch.jsx](src/components/GlobalSearch.jsx) | `GET /search` | [search.py](backend/app/routers/search.py) |
| [Platforms.jsx](src/pages/Platforms.jsx) | `GET /platforms`, `GET /platforms/stats`, `POST /platforms/:id/(dis)connect` | [platforms.py](backend/app/routers/platforms.py) |
| [Analytics.jsx](src/pages/Analytics.jsx) | `GET /analytics/summary`, `/analytics/timeseries` | [analytics.py](backend/app/routers/analytics.py) |
| [AgentBuilder.jsx](src/pages/AgentBuilder.jsx) | `GET /agents`, `POST /agents`, `PATCH /agents/:id`, `POST /agents/:id/test` | [agents.py](backend/app/routers/agents.py) |
| [channels/*](src/pages/channels/) | `/ecommerce/*`, `/apis`, `/integrations`, `/api-logs*`, `/sheets`, `/health/services`, `/incidents` | [channels.py](backend/app/routers/channels.py) |

All write endpoints echo the resulting entity, not `{ok:true}` — mirrors spec §8 "Optimistic UI contract", so the client never refetches after a mutation.

### Optimistic UI contract for messages

Documented in spec §8. The SPA:

1. Generates `tempId = tmp-<ts>-<rand>` and inserts an optimistic bubble with `{ id: tempId, tempId, from: 'me', delivered: false }`.
2. POSTs `/conversations/:id/messages` with that `tempId` in the body.
3. The server persists the row, returns the saved message (still echoing `tempId`), and fans out `message.created` on `conv:<id>` to every other subscriber.
4. The local sender's handler swaps the bubble (`tempId → id`) using the POST response. The incoming WebSocket echo is de-duped against the server-assigned `id`.

If the POST errors, the bubble stays with `{ failed: true }` so the UI can surface a retry.

---

## 3. Realtime (WebSocket)

**Why WebSocket and not SSE** — the spec (§2, §8) mandates realtime on several surfaces: inbox, chat thread, ticket pane, notifications, teams chat, typing, presence. Two properties of WebSocket make it the right default:

- **Bidirectional**: the client subscribes/unsubscribes as it navigates (`{action:'subscribe',topics:[…]}`), so the server only pushes what the open tab cares about. SSE would need a parallel HTTP channel for subscribe frames and a separate endpoint per topic — more moving parts.
- **Single long-lived connection**: less proxy turbulence than many short SSE streams. Works over Vite's proxy out of the box with `ws: true`.

We keep SSE for **one specific place**: streaming AI agent output (spec §10 / §3.11.D). That path is an HTTP POST with `Content-Type: text/event-stream` on the response, which is a natural fit for token-by-token output. It won't be added until Build 2.

### Endpoint

`wss://host/ws?token=<bearer>` — token is validated against `auth_sessions` ([backend/app/routers/auth.py](backend/app/routers/auth.py) `session_user_from_token`). A bad token → close with 1008.

### Frame shapes

Client → server:
```jsonc
{ "action": "subscribe",   "topics": ["inbox:<ws>", "conv:<id>"] }
{ "action": "unsubscribe", "topics": ["conv:<id>"] }
{ "action": "ping" }
```

Server → client:
```jsonc
// on accept
{ "type": "hello", "data": { "user_id": "<uuid>" } }
// on subscribe ack
{ "type": "subscribed", "topics": ["conv:<id>"] }
// domain events — envelope is always: {type, topic, at, data}
{ "type": "message.created", "topic": "conv:abc", "at": "2026-04-23T12:00:00Z",
  "data": { "id": "...", "from": "me", "at": "12:00 PM", "text": "hi", ... } }
```

### Topics (produced + consumed today)

| Topic | Events produced | Consumed by |
|---|---|---|
| `inbox:<workspace_id>` | `conversation.updated`, `ticket.created`, `ticket.updated` | [Conversations.jsx](src/pages/Conversations.jsx), [Tickets.jsx](src/pages/Tickets.jsx) |
| `conv:<conversation_id>` | `message.created`, `typing.started`, `typing.stopped` | [Conversations.jsx](src/pages/Conversations.jsx) (only while the thread is open) |
| `ticket:<ticket_id>` | `ticket.updated`, `ticket.timeline.appended` | [Tickets.jsx](src/pages/Tickets.jsx) (only while the ticket is open) |
| `teams:<channel_or_dm_id>` | `team.message.created`, `team.typing` | (not yet — see [progress.md](progress.md) Teams item) |
| `notif:<user_id>` | `notification.created` | [useNotifications.js](src/hooks/useNotifications.js) |

### Client singleton

[src/hooks/useRealtime.js](src/hooks/useRealtime.js):

- One WebSocket per tab, opened lazily when the first `useRealtimeTopic` mounts.
- Exponential backoff reconnect (`1s, 2s, 4s, …, 15s`) — on reconnect, every topic in the subscription map is re-subscribed automatically.
- Subscribe frames are reference-counted: the server only sees one `subscribe/unsubscribe` per distinct topic, even if several components mount the same topic.
- `closeRealtime()` is called on sign-out so the next user's socket authenticates with their own token.

### Scaling the broker

The backend's [Broker](backend/app/ws.py) is an in-memory map `topic → set[WebSocket]`. That means a message published in process A never reaches a subscriber on process B. Fine for Build 1 (single Uvicorn worker), insufficient for spec §13 "horizontal, stateless" deploys.

To scale:
1. Put a Redis Pub/Sub client in `Broker`. Every `publish()` `PUBLISH`es to `topic:<name>` in Redis. Every pod `PSUBSCRIBE topic:*` and fans out locally.
2. Same interface — callers don't change.
3. For very large fan-out (widget on thousands of customer sites), graduate to NATS or a dedicated WebSocket gateway like Centrifugo.

Prefer this over Postgres `LISTEN/NOTIFY` (mentioned in spec §8) once you have Redis in the stack — `LISTEN` payloads are capped at 8 KB and every pod needs its own connection. Redis handles millions of subs cheaply.

---

## 4. Frontend state management

**Decision: keep it in React, don't add a store library.** Each screen's state falls into four shapes, and local `useState` + a tiny pub/sub singleton is enough for all four:

| Shape | Example | How it's handled |
|---|---|---|
| Server entities scoped to a page | ticket list, conversation list | `useState` in the page, hydrated from `api.get()` on mount, mutated optimistically on user action, patched by WebSocket envelopes in the relevant topic. |
| Cross-page-but-per-user state | notifications feed, unread counters | A tiny store behind `useSyncExternalStore` — `state`, `listeners`, `emit` — in its own module ([useNotifications.js](src/hooks/useNotifications.js)). Hydrates once, stays live via `notif:<user>`. |
| Session / auth | user, token, workspace id | Plain `localStorage['workspacex.auth']` + `loadAuth()` / `saveAuth()` helpers in [AuthPage.jsx](src/pages/AuthPage.jsx). Read directly wherever it's needed. |
| Ephemeral UI (modals, filters, dropdowns) | right-rail panel, filter menu open? | `useState` local to the component. |

**Why not Redux / Zustand / Jotai?** Every non-trivial page-level state here is either (a) the server's truth, echoed into React and patched by WebSocket — which the four-step pattern above handles without a library, or (b) UI-local and short-lived. Adding a store would duplicate server state client-side and force us to write cache-invalidation rules that the WebSocket already gives us for free.

**Where a library would pay off**: if and when we add offline support, multi-tab mutation coherence, or a true undo stack. Until then, the singleton pub/sub in [useRealtime.js](src/hooks/useRealtime.js) + `useSyncExternalStore` covers everything spec §8 demands. If you do reach for one, **Zustand** is the smallest step — keep the WebSocket patching logic the same, just move page-level state into a store.

---

## 5. Object storage — presigned uploads to MinIO

Attachments never travel through FastAPI. The browser:

1. `POST /uploads` with `{name, mime, size}` → gets back `{uploadUrl, publicUrl, key, expires_in}`.
2. Does a direct `PUT uploadUrl` with the file bytes + the right `Content-Type`. Boto3's signature includes `Content-Type` in the signed headers so an incorrect header value on the PUT returns 403.
3. Sends `publicUrl` in the next `POST /conversations/:id/messages` — the attachment becomes part of the message row's `attachments` jsonb column.

`uploadUrl` is signed against the internal MinIO endpoint (`minio:9000` inside docker) but rewritten to the browser-visible host (`S3_PUBLIC_URL`, default `http://localhost:9000`) so the browser can actually reach it. See [backend/app/s3.py](backend/app/s3.py).

Prod swap: change `S3_ENDPOINT` / `S3_PUBLIC_URL` / credentials to point at real S3 (or GCS with HMAC keys). Boto3 is endpoint-driven — no code change needed.

---

## 6. Local dev workflow

Recommended: datastores in Docker, api on the host for fast reload.

```bash
# Terminal A — datastores only
docker compose up -d db minio redis

# Terminal B — backend on host (pip or conda)
cd backend && source .venv/bin/activate      # first time: pip install -r requirements.txt
export DATABASE_URL="postgresql://workspacex:workspacex@localhost:5432/workspacex"
export S3_ENDPOINT="http://localhost:9000"
uvicorn app.main:app --reload --port 8000     # auto-migrates + auto-seeds on empty db

# Terminal C — frontend
npm run dev                                   # http://localhost:5173
```

All-in-Docker alternative: `docker compose up -d db minio redis api worker` then `npm run dev` locally.

Sign in with `demo@workspacex.com / demo`. Open two tabs and reply from one — the other tab's inbox preview, chat pane, and the underlying ticket's timeline all update in <100 ms without a refetch.

### Resetting state

- `docker exec workspacex-db-1 psql -U workspacex -d workspacex -c 'TRUNCATE workspaces CASCADE;'` — wipes all workspace data; next API boot re-seeds.
- `localStorage.clear()` in DevTools — drops the token + forces AuthPage again.
- `docker compose down -v && docker compose up -d db minio redis api worker` — nuke volumes, start clean.

### Inspecting realtime

DevTools → Network → **WS** tab → open `ws`. Every envelope the server pushes shows up as a frame. Send a manual frame to test subscriptions: `{"action":"subscribe","topics":["inbox:<your workspace id>"]}`.

### Inspecting storage

- Postgres: `docker exec -it workspacex-db-1 psql -U workspacex -d workspacex` → `\dt` to list tables.
- MinIO: `http://localhost:9001`, login `workspacex` / `workspacex-secret`, bucket `workspacex`.

---

## 7. Promoting past Build 1

When you're ready to move past the MVP spine (see [progress.md](progress.md) § "What's left"), the swaps are contained:

- **Real migration tool**: Today [backend/app/schema.sql](backend/app/schema.sql) applies on every boot with `IF NOT EXISTS`. For prod, switch to Alembic — keep `schema.sql` as the v0 baseline and write numbered migrations from there. Flip `AUTO_MIGRATE=0` so the api doesn't race with CI migrations.
- **pgvector columns + embeddings**: Add `CREATE EXTENSION vector;` and the `tickets.subject_embedding vector(1536)` / `kb_chunks` tables from spec §7. Rewrite `POST /tickets/similar` to use HNSW cosine search.
- **In-proc broker → Redis Pub/Sub**: Edit `Broker` in [backend/app/ws.py](backend/app/ws.py) — `publish()` publishes to Redis; each pod `PSUBSCRIBE topic:*` and fans out locally. Interface stays the same, caller code doesn't change.
- **MinIO → AWS S3 / GCS**: change `S3_ENDPOINT` + credentials. No code changes.
- **Mock agent test → real orchestrator**: add `pydantic-ai[openai]`, register the tools in spec §3.11.H, and stream SSE into the WS topic `conv:<id>`. Drop a new file under `backend/app/ai/` — everything else stays.

Everything in front of those modules — pages, hooks, routers, WebSocket topics, auth — stays.
