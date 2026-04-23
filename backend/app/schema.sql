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
