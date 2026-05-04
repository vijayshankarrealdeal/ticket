# WorkspaceX backend (FastAPI + Postgres + MinIO)

Scope: end-to-end implementation of Builds 1–3 from [BACKEND_SPEC.md](../BACKEND_SPEC.md). Auth, Customers, Conversations, Messages, Tickets, Notifications feed, Teams chat (channels + DMs), Platforms (OAuth + form-driven connectors), Channels (API library, playground, sheets, health probes), Analytics (CSAT, dashboards), and Search are real implementations backed by Postgres and the WebSocket broker. The only canned-response endpoint left from earlier Builds is `/agents/{id}/test`, which still returns deterministic mock output until the streaming Langflow integration replaces it.

## Run the whole stack

```bash
# from repo root — boots Postgres + Redis + MinIO + api + worker
docker compose up -d db minio redis api worker

# first boot auto-applies backend/app/schema.sql and seeds a demo workspace
docker logs workspacex-api-1 | grep -E "schema|Seeded"
```

- API: `http://localhost:8000` (docs at `/docs`)
- MinIO console: `http://localhost:9001` (login `workspacex` / `workspacex-secret`)
- Postgres: `localhost:5432` (user/password/db all `workspacex`)

Demo login: `demo@workspacex.com` / `demo`.

## Run just the API outside Docker

Useful for fast edit-reload with the datastores still in containers.

### pip

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d db minio redis     # datastores in containers

export DATABASE_URL="postgresql://workspacex:workspacex@localhost:5432/workspacex"
export S3_ENDPOINT="http://localhost:9000"

uvicorn app.main:app --reload --port 8000
```

### conda

```bash
conda env create -f backend/environment.yml
conda activate workspacex

docker compose up -d db minio redis

export DATABASE_URL="postgresql://workspacex:workspacex@localhost:5432/workspacex"
export S3_ENDPOINT="http://localhost:9000"

cd backend && uvicorn app.main:app --reload --port 8000
```

On first boot the app:

1. Waits for Postgres to accept connections (retry loop; handles `docker compose up` race).
2. Applies [app/schema.sql](app/schema.sql) with `IF NOT EXISTS` — idempotent.
3. Runs the demo seed when the `users` table is empty (demo creds above).
4. Ensures the private MinIO bucket `workspacex` exists. Reads go through `GET /uploads/sign?key=…`, which mints a short-lived presigned URL scoped to the caller's workspace.

Set `AUTO_MIGRATE=0` to disable the schema apply (use real migrations in prod). Set `AUTO_SEED=0` to skip the demo seed.

## Stack

- **FastAPI** + **Uvicorn**.
- **Postgres 16 + pgvector** via **asyncpg** ([app/db.py](app/db.py)). A thin wrapper translates `?` placeholders → `$1, $2, …` and preserves the aiosqlite-style `cursor` interface the routers were originally written against. Custom text codecs let us pass ISO-8601 strings for `timestamp/date` columns and receive UUIDs as plain strings.
- **MinIO** (S3-compatible) for presigned uploads ([app/s3.py](app/s3.py)). Swap for AWS S3 / GCS by changing `S3_ENDPOINT` — the code is endpoint-driven.
- **Redis 7** declared in the compose stack; wired in `DATABASE_URL` but only consumed by Build 2+ (OAuth state store, Langflow admin-token cache). The in-process WebSocket broker ([app/ws.py](app/ws.py)) doesn't depend on it.
- **WebSocket** at `/ws` with topic-subscribe (`inbox:<ws>`, `conv:<id>`, `ticket:<id>`, `notif:<user>`, `teams:<id>`). See [../wire.md](../wire.md).
- **Argon2id** for password hashing; 32-byte random bearer tokens stored as `sha256(token)` in `auth_sessions`.

## Layout

```
backend/
├── app/
│   ├── main.py           # FastAPI app, lifespan runs migrate + seed + bucket ensure
│   ├── config.py         # env settings (DATABASE_URL, S3_*, CORS_ORIGINS)
│   ├── db.py             # asyncpg pool, schema applier, DB compat wrapper
│   ├── schema.sql        # authoritative DDL (subset of spec §7)
│   ├── seed.py           # demo workspace fixture (run: python -m app.seed)
│   ├── s3.py             # MinIO bucket bootstrap + presigned PUT helper
│   ├── ws.py             # topic broker + /ws endpoint
│   ├── worker.py         # stub background worker (heartbeat, spec §11)
│   ├── deps.py           # current_user dependency
│   ├── schemas.py        # pydantic request/response models
│   └── routers/
│       ├── auth.py
│       ├── me.py
│       ├── conversations.py
│       ├── tickets.py
│       ├── customers.py
│       ├── teams.py
│       ├── notifications.py
│       ├── search.py
│       ├── platforms.py
│       ├── analytics.py
│       ├── agents.py
│       ├── channels.py
│       └── uploads.py
├── requirements.txt
├── environment.yml       # conda env
├── Dockerfile
└── README.md
```

## Operations

- Reset the DB: `docker exec workspacex-db-1 psql -U workspacex -d workspacex -c 'TRUNCATE workspaces CASCADE;'` — next `api` boot re-seeds.
- Reseed without wiping schema: `docker exec -it workspacex-api-1 python -m app.seed`.
- Inspect WebSocket traffic: DevTools → Network → `ws` entry → Frames.
- Inspect MinIO objects: `http://localhost:9001` → bucket `workspacex`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                           # unit suite (no infra)
TEST_DATABASE_URL=postgres://… pytest -m integration   # DB-backed checks
```

The unit suite covers the placeholder translator, DSN normaliser, widget HMAC signer, and upload allowlist. Integration tests are skipped unless `TEST_DATABASE_URL` is set.
