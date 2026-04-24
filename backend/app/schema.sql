-- Postgres schema — WorkspaceX
-- Authoritative reference: BACKEND_SPEC.md §7. This file is a Build-1 subset:
-- omits pgvector, tickets.subject_embedding, kb_chunks, and the bulk of the
-- analytics/incidents tables until they're actually queried. Every statement
-- is IF NOT EXISTS so startup can run this idempotently on every boot.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- core tenancy ----------
CREATE TABLE IF NOT EXISTS workspaces (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text NOT NULL,
  slug         text UNIQUE NOT NULL,
  timezone     text NOT NULL DEFAULT 'Asia/Kolkata',
  plan         text NOT NULL DEFAULT 'growth',
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name           text NOT NULL,
  email          citext NOT NULL,
  password_hash  text,
  provider       text NOT NULL DEFAULT 'password',
  role           text NOT NULL DEFAULT 'agent',
  status         text NOT NULL DEFAULT 'offline',
  avatar_url     text,
  last_seen_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, email)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash   text NOT NULL UNIQUE,
  user_agent   text,
  expires_at   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auth_sessions_tokenh_idx ON auth_sessions (token_hash);

-- ---------- customers & orders ----------
CREATE TABLE IF NOT EXISTS customers (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            text,
  phone           text,
  email           citext,
  shopify_id      text,
  psid            text,
  wa_id           text,
  since           date,
  address         text,
  other_addresses jsonb NOT NULL DEFAULT '[]'::jsonb,
  orders_count    int NOT NULL DEFAULT 0,
  returns_count   int NOT NULL DEFAULT 0,
  kind            text NOT NULL DEFAULT 'customer',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customers_ws_phone_idx   ON customers (workspace_id, phone);
CREATE INDEX IF NOT EXISTS customers_ws_email_idx   ON customers (workspace_id, email);
CREATE INDEX IF NOT EXISTS customers_name_trgm_idx  ON customers USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS orders (
  id            text PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id   uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  source        text NOT NULL DEFAULT 'shopify',
  placed_at     timestamptz NOT NULL,
  total         numeric(12, 2) NOT NULL,
  currency      char(3) NOT NULL DEFAULT 'INR',
  status        text NOT NULL,
  status_tone   text,
  items         jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_ws_customer_idx ON orders (workspace_id, customer_id, placed_at DESC);

-- ---------- conversations & messages ----------
CREATE TABLE IF NOT EXISTS conversations (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id         uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  channel             text NOT NULL,
  external_thread_id  text,
  assignee_id         uuid REFERENCES users(id),
  status              text NOT NULL DEFAULT 'open',
  preview             text,
  last_message_at     timestamptz,
  last_inbound_at     timestamptz,
  unread_count        int NOT NULL DEFAULT 0,
  order_id            text REFERENCES orders(id),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_ws_last_idx ON conversations (workspace_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS conversations_preview_trgm_idx ON conversations USING gin (preview gin_trgm_ops);

CREATE TABLE IF NOT EXISTS messages (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  conversation_id      uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  from_role            text NOT NULL,
  from_user_id         uuid REFERENCES users(id),
  from_name            text,
  external_message_id  text,
  at                   timestamptz NOT NULL DEFAULT now(),
  text                 text,
  attachments          jsonb NOT NULL DEFAULT '[]'::jsonb,
  reply_to_id          uuid REFERENCES messages(id),
  delivered_at         timestamptz,
  read_at              timestamptz,
  ticket_id            bigint
);
CREATE INDEX IF NOT EXISTS messages_conv_at_idx ON messages (conversation_id, at);
CREATE INDEX IF NOT EXISTS messages_ticket_idx ON messages (ticket_id) WHERE ticket_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_notes (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id  uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  author_id        uuid REFERENCES users(id),
  author_name      text NOT NULL,
  at               timestamptz NOT NULL DEFAULT now(),
  text             text NOT NULL
);

-- ---------- tickets ----------
CREATE TABLE IF NOT EXISTS tickets (
  id                  bigserial PRIMARY KEY,
  workspace_id        uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  customer_id         uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  conversation_id     uuid REFERENCES conversations(id),
  order_id            text REFERENCES orders(id),
  parent_ticket_id    bigint REFERENCES tickets(id),
  status              text NOT NULL DEFAULT 'queued',
  source              text NOT NULL,
  channel             text NOT NULL,
  task                text NOT NULL,
  team                text NOT NULL,
  assignee_id         uuid REFERENCES users(id),
  priority            text NOT NULL DEFAULT 'normal',
  subject             text,
  description         text,
  tags                jsonb NOT NULL DEFAULT '[]'::jsonb,
  custom_fields       jsonb NOT NULL DEFAULT '{}'::jsonb,
  voc                 text,
  resolution_notes    text,
  sla_due_at          timestamptz,
  order_status        text,
  order_placed_on     date,
  intent_key          text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  resolved_at         timestamptz
);
CREATE INDEX IF NOT EXISTS tickets_ws_status_idx   ON tickets (workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS tickets_ws_assignee_idx ON tickets (workspace_id, assignee_id) WHERE assignee_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tickets_customer_idx    ON tickets (customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tickets_subj_trgm_idx   ON tickets USING gin (subject gin_trgm_ops);

CREATE TABLE IF NOT EXISTS ticket_timeline (
  id             bigserial PRIMARY KEY,
  ticket_id      bigint NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  at             timestamptz NOT NULL DEFAULT now(),
  actor          text NOT NULL,
  actor_user_id  uuid REFERENCES users(id),
  text           text NOT NULL
);
CREATE INDEX IF NOT EXISTS ticket_timeline_idx ON ticket_timeline (ticket_id, at);

CREATE TABLE IF NOT EXISTS ticket_notes (
  id         bigserial PRIMARY KEY,
  ticket_id  bigint NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  author_id  uuid REFERENCES users(id),
  author     text NOT NULL,
  at         timestamptz NOT NULL DEFAULT now(),
  text       text NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_child (
  ticket_id       bigint PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
  assignee_email  text,
  team            text,
  objective       text,
  prompt          text,
  resolution      text
);

-- ---------- notifications ----------
CREATE TABLE IF NOT EXISTS notifications (
  id      bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type    text NOT NULL,
  title   text NOT NULL,
  body    text,
  link    text,
  ts      timestamptz NOT NULL DEFAULT now(),
  unread  boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx ON notifications (user_id) WHERE unread;

-- ---------- teams ----------
CREATE TABLE IF NOT EXISTS teams_channels (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text,
  privacy       text NOT NULL DEFAULT 'private',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS teams_dms (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  -- jsonb (not uuid[]) so the router can round-trip via js() / jl() uniformly.
  member_ids    jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teams_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  channel_id    uuid REFERENCES teams_channels(id) ON DELETE CASCADE,
  dm_id         uuid REFERENCES teams_dms(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES users(id),
  at            timestamptz NOT NULL DEFAULT now(),
  text          text,
  blocks        jsonb NOT NULL DEFAULT '[]'::jsonb,
  attachments   jsonb NOT NULL DEFAULT '[]'::jsonb,
  reply_to_id   uuid,
  CHECK ((channel_id IS NOT NULL) <> (dm_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS teams_messages_channel_idx ON teams_messages (channel_id, at);
CREATE INDEX IF NOT EXISTS teams_messages_dm_idx ON teams_messages (dm_id, at);

CREATE TABLE IF NOT EXISTS teams_reactions (
  message_id  uuid NOT NULL REFERENCES teams_messages(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  emoji       text NOT NULL,
  PRIMARY KEY (message_id, user_id, emoji)
);

-- ---------- platforms & agents ----------
-- Platforms use slug IDs ("whatsapp", "gmail", ...) that map 1:1 to the
-- DEFAULT_CATALOG in routers/platforms.py. A slug can exist once per workspace.
CREATE TABLE IF NOT EXISTS platforms (
  id            text NOT NULL,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind          text NOT NULL,
  name          text NOT NULL,
  description   text,
  status        text NOT NULL DEFAULT 'disconnected',
  tickets_count int NOT NULL DEFAULT 0,
  config        jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS agents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name              text NOT NULL,
  model             text NOT NULL DEFAULT 'gpt-4.1',
  persona           text NOT NULL DEFAULT '',
  instructions      text NOT NULL DEFAULT '',
  temperature       numeric(3, 2) NOT NULL DEFAULT 0.3,
  tools             jsonb NOT NULL DEFAULT '[]'::jsonb,
  status            text NOT NULL DEFAULT 'draft',
  active_version    int NOT NULL DEFAULT 1,
  last_published_at timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- ---------- API Library / Integrations / Logs (spec §3.9.3, §3.9.4) ----------
CREATE TABLE IF NOT EXISTS integrations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  description   text,
  kind          text NOT NULL DEFAULT 'custom',
  config        jsonb NOT NULL DEFAULT '{}'::jsonb,
  health        text NOT NULL DEFAULT 'good',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS integrations_ws_idx ON integrations (workspace_id);

CREATE TABLE IF NOT EXISTS apis (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  integration_id  uuid REFERENCES integrations(id) ON DELETE SET NULL,
  name            text NOT NULL,
  method          text NOT NULL DEFAULT 'GET',
  description     text,
  spec            jsonb NOT NULL DEFAULT '{}'::jsonb,
  calls_24h       int NOT NULL DEFAULT 0,
  p95_ms          int NOT NULL DEFAULT 0,
  last_used_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS apis_ws_idx ON apis (workspace_id);
CREATE INDEX IF NOT EXISTS apis_ws_name_idx ON apis (workspace_id, name);

CREATE TABLE IF NOT EXISTS api_logs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  api_id        uuid REFERENCES apis(id) ON DELETE SET NULL,
  customer_id   uuid REFERENCES customers(id),
  trace_id      text NOT NULL,
  status        int NOT NULL,
  latency_ms    int NOT NULL DEFAULT 0,
  request       jsonb,
  response      jsonb,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_logs_ws_at_idx  ON api_logs (workspace_id, at DESC);
CREATE INDEX IF NOT EXISTS api_logs_api_at_idx ON api_logs (api_id, at DESC);

-- ---------- E-commerce stores (spec §3.9.2) ----------
CREATE TABLE IF NOT EXISTS ecommerce_stores (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  platform       text NOT NULL,
  name           text NOT NULL,
  health         int  NOT NULL DEFAULT 100,
  orders_30d     int  NOT NULL DEFAULT 0,
  last_sync_at   timestamptz,
  status         text NOT NULL DEFAULT 'connected',
  config         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------- Outbox dispatcher (spec §11) ----------
CREATE TABLE IF NOT EXISTS outbox (
  id           bigserial PRIMARY KEY,
  workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE,
  kind         text NOT NULL,
  payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
  status       text NOT NULL DEFAULT 'pending',
  attempts     int  NOT NULL DEFAULT 0,
  not_before   timestamptz NOT NULL DEFAULT now(),
  last_error   text,
  locked_by    text,
  locked_at    timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbox_due_idx ON outbox (status, not_before) WHERE status = 'pending';

-- ---------- Agent runs + Langflow snapshots (spec §3.11.C) ----------
CREATE TABLE IF NOT EXISTS agent_runs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  agent_id        uuid REFERENCES agents(id) ON DELETE SET NULL,
  conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
  version         int  NOT NULL DEFAULT 0,
  input           jsonb NOT NULL DEFAULT '{}'::jsonb,
  output          jsonb,
  status          text NOT NULL DEFAULT 'ok',
  latency_ms      int,
  tokens_in       int,
  tokens_out      int,
  cost_usd        numeric(10, 4),
  trace           jsonb,
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_agent_at_idx ON agent_runs (agent_id, at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_ws_at_idx    ON agent_runs (workspace_id, at DESC);

CREATE TABLE IF NOT EXISTS agent_flow_snapshots (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  version     int  NOT NULL,
  flow_json   jsonb NOT NULL,
  created_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agent_id, version)
);

-- ---------- Password resets / OAuth / Langflow columns (spec §3.1, §3.11.C) ----------
CREATE TABLE IF NOT EXISTS password_resets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------- Additive columns on existing tables ----------
-- Users: OAuth identity + Langflow per-user binding.
ALTER TABLE users           ADD COLUMN IF NOT EXISTS oauth_sub text;
ALTER TABLE users           ADD COLUMN IF NOT EXISTS langflow_user_id uuid;
ALTER TABLE users           ADD COLUMN IF NOT EXISTS langflow_password_secret_id text;
CREATE UNIQUE INDEX IF NOT EXISTS users_provider_sub_idx
  ON users (workspace_id, provider, oauth_sub) WHERE oauth_sub IS NOT NULL;

-- Workspaces: AI guardrails + Langflow folder.
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS default_lang text NOT NULL DEFAULT 'en';
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS ai_prompt_append text;
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS ai_allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS ai_force_default boolean NOT NULL DEFAULT false;
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS intent_rules jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workspaces      ADD COLUMN IF NOT EXISTS langflow_folder_id uuid;

-- Agents: Langflow bindings + schemas.
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'builtin';
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS langflow_flow_id uuid;
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS langflow_folder_id uuid;
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS input_schema  jsonb;
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS output_schema jsonb;
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES users(id);
ALTER TABLE agents          ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

-- Tickets: intent classifier columns (spec §3.4.1).
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS intent_confidence numeric(3, 2);
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS order_ref        text;
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS last_customer_at timestamptz;
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS auto_close_at    timestamptz;

-- Notifications: prefs blob on users.
ALTER TABLE users           ADD COLUMN IF NOT EXISTS notification_prefs jsonb NOT NULL DEFAULT '{"email":true,"browser":true,"digest":"daily"}'::jsonb;

-- TOTP / 2FA columns on users (spec §3.1, Build 4).
-- `totp_secret_pending` is staged during /me/2fa/setup and only promoted to
-- `totp_secret` after the user successfully verifies a code.
ALTER TABLE users           ADD COLUMN IF NOT EXISTS totp_secret text;
ALTER TABLE users           ADD COLUMN IF NOT EXISTS totp_secret_pending text;
ALTER TABLE users           ADD COLUMN IF NOT EXISTS totp_enabled boolean NOT NULL DEFAULT false;

-- Conversations: external-channel recordings reference.
ALTER TABLE conversations   ADD COLUMN IF NOT EXISTS recordings jsonb NOT NULL DEFAULT '[]'::jsonb;
-- Email threads require the outbound Subject to match the thread's subject
-- for Gmail's threadId to actually stitch the reply in. Store it here so
-- `send_reply` in the AI orchestrator can look it up without calling
-- messages.get again.
ALTER TABLE conversations   ADD COLUMN IF NOT EXISTS subject text;

-- Messages: track which AI agent drafted an `ai`-role reply (spec §3.11.F).
ALTER TABLE messages        ADD COLUMN IF NOT EXISTS ai_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL;

-- ---------- KB chunks (spec §7, §3.11.H kb_search tool) ----------
-- Build-2 subset: ILIKE-over-body until pgvector lands. When vector is enabled,
-- promote `embedding bytea` to `vector(1536)` + add the HNSW index from §9.
CREATE TABLE IF NOT EXISTS kb_chunks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source        text NOT NULL,
  title         text,
  body          text NOT NULL,
  embedding     bytea,
  embedded_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_chunks_ws_idx       ON kb_chunks (workspace_id);
CREATE INDEX IF NOT EXISTS kb_chunks_body_trgm_idx ON kb_chunks USING gin (body gin_trgm_ops);
ALTER TABLE kb_chunks       ADD COLUMN IF NOT EXISTS embedded_at timestamptz;
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS subject_embedding bytea;
ALTER TABLE tickets         ADD COLUMN IF NOT EXISTS embedded_at timestamptz;

-- ---------- SLA policy (spec §3.4, §11) ----------
-- Per-team-per-task SLA hours. Used by POST /tickets to compute sla_due_at.
CREATE TABLE IF NOT EXISTS sla_policies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  team          text,
  task          text,
  hours         numeric(6, 2) NOT NULL DEFAULT 24,
  priority      text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, team, task, priority)
);
CREATE INDEX IF NOT EXISTS sla_policies_ws_idx ON sla_policies (workspace_id);

-- ---------- Teams read cursors + presence (spec §3.6) ----------
CREATE TABLE IF NOT EXISTS teams_read_cursors (
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel_id    uuid REFERENCES teams_channels(id) ON DELETE CASCADE,
  dm_id         uuid REFERENCES teams_dms(id) ON DELETE CASCADE,
  last_read_at  timestamptz NOT NULL DEFAULT now(),
  CHECK ((channel_id IS NOT NULL) <> (dm_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS teams_read_cursors_chan_idx
  ON teams_read_cursors (user_id, channel_id) WHERE channel_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS teams_read_cursors_dm_idx
  ON teams_read_cursors (user_id, dm_id) WHERE dm_id IS NOT NULL;

-- ---------- Health services + incidents (spec §3.9.6) ----------
CREATE TABLE IF NOT EXISTS health_services (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            text NOT NULL,
  category        text,
  status          text NOT NULL DEFAULT 'operational',
  uptime_30d      numeric(5, 2) NOT NULL DEFAULT 100,
  latency_p95_ms  int NOT NULL DEFAULT 0,
  error_rate      numeric(5, 2) NOT NULL DEFAULT 0,
  last_check_at   timestamptz,
  accent          text,
  letter          text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS incidents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title         text NOT NULL,
  status        text NOT NULL DEFAULT 'investigating',
  tone          text NOT NULL DEFAULT 'warning',
  components    jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary       text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz
);

CREATE TABLE IF NOT EXISTS incident_subscribers (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email         citext NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, email)
);

-- ---------- CSAT surveys (spec §3.10) ----------
CREATE TABLE IF NOT EXISTS csat_responses (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  ticket_id     bigint REFERENCES tickets(id) ON DELETE CASCADE,
  customer_id   uuid REFERENCES customers(id),
  rating        int NOT NULL,
  comment       text,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS csat_ws_at_idx ON csat_responses (workspace_id, at DESC);

-- Tokens issued on ticket-resolve so the survey URL can be rendered without
-- exposing ticket IDs to the public CSAT form.
CREATE TABLE IF NOT EXISTS csat_tokens (
  token         text PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  ticket_id     bigint NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  customer_id   uuid REFERENCES customers(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  submitted_at  timestamptz
);
CREATE INDEX IF NOT EXISTS csat_tokens_ticket_idx ON csat_tokens (ticket_id);

-- ---------- Sheets (spec §3.9.5) — table for when the sync worker lands ----------
CREATE TABLE IF NOT EXISTS sheets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  url           text NOT NULL,
  name          text,
  tabs          jsonb NOT NULL DEFAULT '[]'::jsonb,
  rows          int NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'connected',
  last_sync_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------- E-commerce events feed (spec §3.9.2) ----------
CREATE TABLE IF NOT EXISTS ecommerce_events (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  store_id      uuid REFERENCES ecommerce_stores(id) ON DELETE SET NULL,
  type          text NOT NULL,
  title         text NOT NULL,
  sub           text,
  tone          text,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ecommerce_events_ws_at_idx ON ecommerce_events (workspace_id, at DESC);

-- ---------- Idempotency keys (spec cross-cutting §3.3, §3.4) ----------
-- Scoped per user+endpoint so repeated POSTs from a flaky webhook / network
-- don't create duplicate tickets or messages. 24h TTL is enforced by a
-- cleanup pass in the worker.
CREATE TABLE IF NOT EXISTS idempotency_keys (
  id            bigserial PRIMARY KEY,
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id       uuid REFERENCES users(id) ON DELETE CASCADE,
  scope         text NOT NULL,
  key           text NOT NULL,
  response      jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, scope, key)
);
CREATE INDEX IF NOT EXISTS idem_ws_scope_created_idx
  ON idempotency_keys (workspace_id, scope, created_at DESC);

-- ---------- Workspace counters (spec §9 cache layer) ----------
-- Pre-aggregated "how many open tickets / unread conversations / pending
-- notifications per workspace" keyed by kind. Refreshed by worker sweep;
-- served from /workspace/counters so the sidebar badges don't re-count on
-- every page render.
CREATE TABLE IF NOT EXISTS workspace_counters (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind          text NOT NULL,
  value         bigint NOT NULL DEFAULT 0,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, kind)
);

-- ---------- Analytics daily rollup (spec §3.10, §11) ----------
-- Nightly aggregator worker folds /analytics/summary + /timeseries into here;
-- Analytics.jsx falls back to live queries when a given day is missing so a
-- fresh install doesn't render empty charts.
CREATE TABLE IF NOT EXISTS analytics_daily (
  workspace_id       uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  day                date NOT NULL,
  tickets_created    int  NOT NULL DEFAULT 0,
  tickets_resolved   int  NOT NULL DEFAULT 0,
  avg_resolution_h   numeric(8, 2),
  csat_avg           numeric(4, 2),
  ai_deflection_pct  numeric(5, 2),
  refreshed_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, day)
);

-- ---------- Delivery log (spec §11 outbox) ----------
-- Every outbound dispatch (email / webhook / channel send) writes one row so
-- admins can see what was attempted, what failed, and what's still pending.
-- Drives the Settings → Notifications → "Recent deliveries" panel and the
-- forgot-password / broadcast flows in dev where SMTP isn't wired.
CREATE TABLE IF NOT EXISTS delivery_log (
  id            bigserial PRIMARY KEY,
  workspace_id  uuid REFERENCES workspaces(id) ON DELETE CASCADE,
  kind          text NOT NULL,
  target        text NOT NULL,
  subject       text,
  status        text NOT NULL DEFAULT 'sent',
  error         text,
  payload       jsonb,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS delivery_log_ws_at_idx ON delivery_log (workspace_id, at DESC);


-- ---------- Web widget visitors (spec §6.5) ----------
-- One row per unique browser session for the embedded widget. `visitor_id` is
-- a stable client-generated id so a returning visitor lands in the same
-- conversation. We store `customer_id` once we have enough signal (email /
-- phone) to merge the visitor into the customer directory.
CREATE TABLE IF NOT EXISTS widget_visitors (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  visitor_id      text NOT NULL,
  customer_id     uuid REFERENCES customers(id) ON DELETE SET NULL,
  conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
  user_agent      text,
  ip              text,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, visitor_id)
);


-- ---------- Platform credentials (spec §6, §3.1 OAuth channels) ----------
-- One row per connected channel account. `secrets` holds the access/refresh
-- token blob; in prod we'll `pgp_sym_encrypt` it per workspace key. `meta`
-- carries non-secret scaffolding (phone_number_id, page_id, shop domain, etc.).
CREATE TABLE IF NOT EXISTS platform_credentials (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  provider       text NOT NULL,           -- whatsapp | gmail | outlook | instagram | shopify | slack | twilio
  account_id     text,                    -- provider-scoped id (phone_number_id / email / page_id / shop / team_id)
  status         text NOT NULL DEFAULT 'active',
  secrets        jsonb NOT NULL DEFAULT '{}'::jsonb,
  meta           jsonb NOT NULL DEFAULT '{}'::jsonb,
  installed_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  expires_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, provider, account_id)
);
CREATE INDEX IF NOT EXISTS platform_credentials_ws_idx ON platform_credentials (workspace_id);
CREATE INDEX IF NOT EXISTS platform_credentials_provider_idx ON platform_credentials (provider);

-- ---------- OAuth state cache (spec §3.1) ----------
-- PKCE verifier + nonce + redirect URL scoped to the browser that started the
-- flow. Consumed once on callback and then dropped. TTL enforced by a worker
-- sweep (10 min is enough for any well-behaved OAuth dance).
CREATE TABLE IF NOT EXISTS oauth_states (
  state          text PRIMARY KEY,
  provider       text NOT NULL,
  mode           text NOT NULL DEFAULT 'signin',   -- signin | connect
  workspace_id   uuid REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id        uuid REFERENCES users(id) ON DELETE CASCADE,
  code_verifier  text,
  nonce          text,
  redirect_uri   text,
  return_to      text,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oauth_states_created_idx ON oauth_states (created_at);

-- ---------- Billing invoices (spec §3.12) ----------
-- Mirrors the rows Stripe would send via webhook; `/billing/invoices` reads
-- from here and the webhook endpoint upserts.
CREATE TABLE IF NOT EXISTS invoices (
  id            text PRIMARY KEY,          -- `in_...` (stripe id or synthetic)
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  number        text,
  amount_cents  int NOT NULL DEFAULT 0,
  currency      text NOT NULL DEFAULT 'usd',
  status        text NOT NULL DEFAULT 'paid',  -- paid | open | void | uncollectible
  period_start  timestamptz,
  period_end    timestamptz,
  hosted_url    text,
  pdf_url       text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS invoices_ws_idx ON invoices (workspace_id, created_at DESC);

-- Stripe customer id / subscription id — stored on workspace so we can hit
-- Stripe in the billing endpoints once keys are configured.
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS stripe_customer_id text;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS stripe_subscription_id text;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS plan_renews_at timestamptz;

-- ---------- Broadcast messages (spec §3.7 broadcast generator) ----------
-- Admin-authored announcements fanned out to every workspace member's
-- notification feed. Row is kept so we can render a "Recent broadcasts"
-- section in Settings → Broadcasts later without re-querying notifications.
CREATE TABLE IF NOT EXISTS broadcasts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  author_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  title         text NOT NULL,
  body          text,
  link          text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS broadcasts_ws_idx ON broadcasts (workspace_id, created_at DESC);

-- ---------- Channel subscriptions (spec §11 watch renewer) ----------
-- Tracks provider-side push subscriptions that need periodic renewal:
-- Gmail `users.watch` (expires after 7 days), Outlook Graph subscriptions
-- (expires after 3 days), Shopify webhook registrations. The worker's daily
-- watch-renewer tick reads from here and re-creates anything within 24h of
-- its expiry. `provider_sub_id` is the provider-side identifier (Graph
-- subscription id, Gmail historyId anchor, Shopify webhook id).
CREATE TABLE IF NOT EXISTS channel_subscriptions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  credential_id   uuid REFERENCES platform_credentials(id) ON DELETE CASCADE,
  provider        text NOT NULL,              -- gmail | outlook | shopify
  topic           text,                       -- Pub/Sub topic / Graph resource / Shopify topic
  provider_sub_id text,                       -- provider-assigned id
  history_id      text,                       -- Gmail historyId anchor
  status          text NOT NULL DEFAULT 'active', -- active | expired | error | revoked
  last_renewed_at timestamptz,
  expires_at      timestamptz,
  error           text,
  meta            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS channel_subs_ws_idx      ON channel_subscriptions (workspace_id);
CREATE INDEX IF NOT EXISTS channel_subs_renew_idx   ON channel_subscriptions (expires_at)
  WHERE status = 'active';

-- ---------- Webhook rate-limit buckets (spec §9 rate-limiter) ----------
-- Simple token-bucket state per (provider, workspace). `tokens` decays with
-- time; every inbound webhook debits one and rejects with 429 if the bucket
-- is empty. `capacity` + `refill_per_sec` defaulted at the router level so a
-- missing row behaves like a full bucket.
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
  bucket_key      text PRIMARY KEY,          -- e.g. "whatsapp:<ws_id>" or "shopify:<shop>"
  tokens          double precision NOT NULL DEFAULT 60,
  capacity        double precision NOT NULL DEFAULT 60,
  refill_per_sec  double precision NOT NULL DEFAULT 1,
  last_refill_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------- Langflow publish snapshots (spec §3.11.C) ----------
-- The Langflow → FastAPI publish hook writes one row per agent+version so the
-- AgentBuilder can diff versions and the orchestrator can pin to a specific
-- flow snapshot. Already referenced by agents.py; kept as `agent_flow_snapshots`
-- which lives earlier in this file — this is the companion index + metadata.
ALTER TABLE agent_flow_snapshots ADD COLUMN IF NOT EXISTS lf_folder_id text;
ALTER TABLE agent_flow_snapshots ADD COLUMN IF NOT EXISTS lf_flow_id   text;
ALTER TABLE agent_flow_snapshots ADD COLUMN IF NOT EXISTS lf_version   int;
CREATE INDEX IF NOT EXISTS agent_flow_snapshots_agent_idx
  ON agent_flow_snapshots (agent_id, created_at DESC);

-- ---------- pgvector opt-in (spec §7) ----------
-- Embedding columns remain `bytea` by default (deterministic hashed sketch) so
-- a vanilla Postgres install keeps working. Set `ENABLE_PGVECTOR=1` during
-- startup and run the migration file [backend/app/migrations/0002_pgvector.sql]
-- once. We don't `CREATE EXTENSION` here because it requires superuser on
-- most managed Postgres offerings; the app's schema-apply runs as a
-- non-superuser by default.

-- ---------- API Playground (folders / endpoints / environments / variables) ----------
-- Postman-style workspace for ad-hoc HTTP requests. Folders group related
-- endpoints; environments hold {{token}} variables the send pipeline
-- substitutes at call time. `output_schema` is populated on first successful
-- send via a best-effort JSON-schema inference so agents can bind to it later.
CREATE TABLE IF NOT EXISTS api_pg_folders (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  parent_id     uuid REFERENCES api_pg_folders(id) ON DELETE CASCADE,
  sort_order    int NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_pg_folders_ws_idx ON api_pg_folders (workspace_id);

CREATE TABLE IF NOT EXISTS api_pg_endpoints (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  folder_id      uuid REFERENCES api_pg_folders(id) ON DELETE CASCADE,
  name           text NOT NULL,
  description    text NOT NULL DEFAULT '',
  method         text NOT NULL DEFAULT 'GET',
  url            text NOT NULL DEFAULT '',
  headers        jsonb NOT NULL DEFAULT '[]'::jsonb,
  body           text,
  output_schema  jsonb,
  last_status    int,
  last_run_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_pg_endpoints_ws_idx      ON api_pg_endpoints (workspace_id);
CREATE INDEX IF NOT EXISTS api_pg_endpoints_folder_idx  ON api_pg_endpoints (folder_id);
-- Backfill for installs that predate the description column. The SPA + router
-- treat description as mandatory at the API boundary, so existing rows land
-- with an empty string until the operator edits them.
ALTER TABLE api_pg_endpoints ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';

-- Snapshot of everything an agent runtime needs to invoke this endpoint as an
-- LLM tool: {name, description, method, url, headers, body, outputPaths}. Kept
-- as a denormalized JSONB so tool callers don't have to re-derive the
-- included-paths list from the larger output_schema on every request.
ALTER TABLE api_pg_endpoints ADD COLUMN IF NOT EXISTS tool_spec jsonb;

CREATE TABLE IF NOT EXISTS api_pg_environments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);
CREATE INDEX IF NOT EXISTS api_pg_env_ws_idx ON api_pg_environments (workspace_id);

CREATE TABLE IF NOT EXISTS api_pg_variables (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  environment_id  uuid NOT NULL REFERENCES api_pg_environments(id) ON DELETE CASCADE,
  key             text NOT NULL,
  value           text NOT NULL DEFAULT '',
  secret          boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (environment_id, key)
);
CREATE INDEX IF NOT EXISTS api_pg_vars_env_idx ON api_pg_variables (environment_id);

-- ---------- Secret encryption wrapper helpers (spec §7 pgp_sym_encrypt) ----------
-- Thin SQL helpers that use `pgcrypto.pgp_sym_encrypt` when
-- `WORKSPACEX_SECRET_KEY` is set at the session level. Routers call
-- `SELECT secret_wrap($1, current_setting('app.secret_key', true))` to get
-- an encrypted TEXT back; reads go the other way with `secret_unwrap`.
-- No-op passthrough when no key is set so dev installs don't break.
CREATE OR REPLACE FUNCTION secret_wrap(plaintext text, key text)
RETURNS text LANGUAGE plpgsql AS $$
BEGIN
  IF key IS NULL OR length(key) = 0 OR plaintext IS NULL THEN
    RETURN plaintext;
  END IF;
  RETURN encode(pgp_sym_encrypt(plaintext, key), 'base64');
END $$;

CREATE OR REPLACE FUNCTION secret_unwrap(ciphertext text, key text)
RETURNS text LANGUAGE plpgsql AS $$
BEGIN
  IF key IS NULL OR length(key) = 0 OR ciphertext IS NULL THEN
    RETURN ciphertext;
  END IF;
  BEGIN
    RETURN pgp_sym_decrypt(decode(ciphertext, 'base64'), key);
  EXCEPTION WHEN OTHERS THEN
    -- Ciphertext may already be plaintext from a pre-encryption install.
    RETURN ciphertext;
  END;
END $$;
