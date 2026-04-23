# app.md — How the app works

> A product-level tour of WorkspaceX: what it is, who uses it, and what happens end-to-end when a customer messages in. For the technical wiring between SPA and API see [wire.md](wire.md); for the API contract see [BACKEND_SPEC.md](BACKEND_SPEC.md); for implementation status see [progress.md](progress.md).

---

## 1. What the app is

WorkspaceX is a multi-channel customer-support workspace. One inbox, one ticket pipeline, one agent roster — fed by WhatsApp, Email, Instagram DM, a web widget, voice, and any HTTP API the workspace registers. An AI agent can draft, tool-call, refund, or escalate inside the same loop a human agent works in.

Three people use it:

- **Support agents** live in [Conversations](src/pages/Conversations.jsx) and [Tickets](src/pages/Tickets.jsx) all day. They reply to customers, take private notes, pull up order history, and close or escalate tickets.
- **Admins / leads** set up channels in [Platforms](src/pages/Platforms.jsx), write rules in [Agent Builder](src/pages/AgentBuilder.jsx), wire third-party HTTP in [Channels → API Library](src/pages/channels/ApiLibrary.jsx), and watch [Analytics](src/pages/Analytics.jsx).
- **Customers** never log in. They message from WhatsApp / email / the web widget, get replies (from an AI, an agent, or both), and eventually fill out a CSAT form at a public unauthenticated URL ([PublicCsat.jsx](src/pages/PublicCsat.jsx)).

---

## 2. Top-level map

```
Sidebar                 Main workspace
───────                 ──────────────
Conversations   ──►  live inbox + chat pane          (src/pages/Conversations.jsx)
Tickets         ──►  filterable pipeline + detail    (src/pages/Tickets.jsx)
Customers       ──►  customer directory              (src/pages/Customers.jsx)
Teams           ──►  internal chat (channels + DMs)  (src/pages/Teams.jsx)
Platforms       ──►  connect WhatsApp / Email / …    (src/pages/Platforms.jsx)
Channels        ──►  E-Commerce / APIs / Logs /      (src/pages/channels/*)
                     Sheets / Health
Agent Builder   ──►  configure AI agents             (src/pages/AgentBuilder.jsx)
Analytics       ──►  tickets / resolution / CSAT     (src/pages/Analytics.jsx)
Notifications   ──►  assign, mention, reply, SLA     (src/pages/Notifications.jsx)
Settings        ──►  profile, workspace, 2FA…        (src/pages/Settings.jsx)
```

Routing lives in [App.jsx](src/App.jsx). Default landing after auth is `/conversations`.

---

## 3. The core loop — one message, end to end

This is the path that defines the product. Every other feature hangs off this flow.

```
    ┌─────────────────────────────────────────────────────────────────┐
    │                    Customer sends a message                     │
    │       (WhatsApp / Email / Instagram DM / Web widget)            │
    └────────────────────┬────────────────────────────────────────────┘
                         │  channel webhook
                         ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ POST /conversations/:id/inbound                                 │
    │  1. append message to conversations.messages                    │
    │  2. classify intent (Stage-1 keywords → Stage-2 LLM if unsure)  │
    │  3. decide attach | reopen | create ticket                      │
    │  4. fan out conversation.updated on inbox:<ws>                  │
    │  5. fan out message.created on conv:<id>                        │
    │  6. notify the ticket's assignee (if any) via notif:<user>      │
    └────────────────────┬────────────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
    AI reply path                 Human agent path
    (if workspace opts in)        (if no AI or AI escalated)
    ┌─────────────────┐           ┌─────────────────┐
    │ orchestrator    │           │ agent opens the │
    │ picks tools,    │           │ thread, reads   │
    │ calls APIs,     │           │ customer + order│
    │ drafts reply    │           │ context, types  │
    │ or escalates    │           │ a reply         │
    └────────┬────────┘           └────────┬────────┘
             │                             │
             ▼                             ▼
    POST /conversations/:id/messages ← same endpoint either way
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ Server persists, echoes the row, fans out:                      │
    │  - message.created on conv:<id>  → every open chat pane         │
    │  - conversation.updated on inbox:<ws> → every inbox preview     │
    │  - ticket.timeline.appended on ticket:<id> → "Agent X replied   │
    │    on <channel>" in the linked ticket's timeline                │
    └─────────────────────────────────────────────────────────────────┘
```

Everything downstream — analytics, CSAT, SLA warnings, deflection rate — is computed from these same rows.

---

## 4. What each page actually does

### Conversations — the working surface

[src/pages/Conversations.jsx](src/pages/Conversations.jsx). Three columns:

1. **Thread list** (left) — filter by channel + tab (open / waiting / closed / mine). Live-updates via `inbox:<ws>`.
2. **Chat pane** (center) — messages in order, attachments inline, typing indicators, composer with:
   - `AI Draft` chip — calls `POST /ai/draft`, drops the suggestion into the composer so the agent can edit before sending.
   - `Simulate inbound` chip — posts synthetic customer text to `POST /conversations/:id/inbound` to test the intent classifier live.
   - File menu — presigns to MinIO, uploads direct from browser, attaches by public URL.
3. **Right rail** (right) — customer card, recent orders, linked tickets, private notes. Private notes are internal-only and post to `POST /conversations/:id/notes`.

Send path is optimistic: bubble renders before the POST completes; server echoes back with a real id; WebSocket echo is de-duped against the same id. If the POST fails, the bubble stays with a `failed` state so the agent can retry.

### Tickets — the pipeline

[src/pages/Tickets.jsx](src/pages/Tickets.jsx). Filterable by status (`assigned / queued / blocked / waiting_for_customer / resolved`), priority, assignee, channel. Detail pane has three tabs:

- **Conversation** — renders the linked conversation inline so the agent doesn't have to page-hop.
- **Timeline** — every state change, assignment, reply, and SLA warning in order.
- **Notes** — internal ticket-level notes (distinct from conversation notes).

Actions that change ticket state (merge, split, attach message, resolve, reassign) each have a modal. Every action writes a timeline entry and fans out `ticket.updated` + `ticket.timeline.appended`.

Ticket lifecycle:
```
 create ──► assigned ──► in_progress ──► waiting_for_customer ──► resolved
                │             │                  │
                ▼             ▼                  ▼
            queued        blocked          (auto-close after 72h idle)
```
Auto-close worker moves `assigned/in_progress` → `waiting_for_customer` after 24 h of no customer message, and `waiting_for_customer` → `resolved` after 72 h. SLA engine warns 30 min before `sla_due_at` (default `created_at + 24h`).

### Customers

[src/pages/Customers.jsx](src/pages/Customers.jsx). Directory of everyone the workspace has spoken to. Detail view shows recent orders, ticket history, and which channels they're reachable on (so the Conversations header can "hop to WhatsApp" if the same customer emailed in).

### Teams — internal chat

[src/pages/Teams.jsx](src/pages/Teams.jsx). Channels + DMs, @mentions, typing indicators, presence (heartbeat every 30 s, marked offline after 2 min), unread read-cursors. `@Name` in a message turns into a `mention` notification on `notif:<user>`.

This is not customer-facing — it's where support agents coordinate about customer-facing work.

### Platforms

[src/pages/Platforms.jsx](src/pages/Platforms.jsx). The catalog: WhatsApp Business, Gmail, Outlook, Instagram DM, Web widget, Voice (Exotel / Twilio), Slack. Connect flips per-workspace state; for OAuth channels a Manage modal walks through the round-trip.

### Channels (the sub-menu)

Different from "Platforms" (inbound channels). This is the operator-side plumbing:

- **E-Commerce** — connect Shopify / WooCommerce, backfill orders, listen for `orders/*` webhooks.
- **API Library** — register HTTP APIs the AI agent (and human agents via a side-panel) can call by name. `POST /internal/apis/:name` is the actual executor; every call writes an `api_logs` row.
- **API Logs** — paginated log of every tool call, filterable by status-class and api name. Click any row for full request/response detail.
- **Google Sheets** — a Sheet URL becomes a knowledge-base source; rows feed `kb_chunks` for the agent's `kb_search` tool.
- **Health** — per-integration uptime / p95 / error-rate plus an incidents feed. `Re-run checks` triggers `POST /health/probe`.

### Agent Builder

[src/pages/AgentBuilder.jsx](src/pages/AgentBuilder.jsx). Configure an AI agent (model, persona, allowed tools, prompt appendix, guardrails). `Save` posts to `/agents`; `Test` runs it against a seeded scenario and shows output + latency + trace id inline. The default orchestrator ships with tools for `find_customer / get_order / list_customer_orders / kb_search / similar_tickets / send_reply / issue_refund / escalate_to_human / run_library_api`.

For advanced flows, the admin can jump to Langflow (visual agent builder) via `GET /flow/launch`; a published flow hits `POST /webhooks/langflow/published` and becomes selectable in the inbound pipeline.

### Analytics

[src/pages/Analytics.jsx](src/pages/Analytics.jsx). Summary cards (tickets this week, avg resolution, CSAT, AI deflection) + a 14-day bar chart you can flip between metrics. All four metrics are computed from live queries today; `analytics_daily` rollups are deferred until volume demands it.

### Notifications

[src/pages/Notifications.jsx](src/pages/Notifications.jsx) + a bell in [TopBar.jsx](src/components/TopBar.jsx). Types:

- `assign` — you got assigned a ticket.
- `reply` — a customer replied on a ticket assigned to you.
- `mention` — someone @'d you in Teams.
- `sla` — a ticket is within 30 min of SLA breach.
- `broadcast` / `teammate_joined` — workspace-wide, deferred.

Each notification deep-links to the relevant conversation / ticket / team thread. Lives on `notif:<user_id>`, hydrated once by [useNotifications.js](src/hooks/useNotifications.js) and kept live by WebSocket.

### Settings

[src/pages/Settings.jsx](src/pages/Settings.jsx). Profile, workspace, notification prefs (including email digest), security (password), billing (Build 4). Each form saves independently.

---

## 5. How AI plugs in

The AI agent is another participant in the conversation — it uses the same `POST /conversations/:id/messages` endpoint a human would. What makes it AI:

1. **Intent classifier** (`backend/app/routers/tickets.py`) runs on every inbound. Stage 1 is deterministic keyword + order-ref matching; if confidence < 0.6 and `OPENAI_API_KEY` is set, Stage 2 asks `gpt-4.1-mini` for a structured classification.
2. **Orchestrator** (`backend/app/ai/orchestrator.py`) is a `pydantic-ai` typed-tool agent. Tools are real HTTP calls into the same backend (`find_customer`, `get_order`, `kb_search`, etc.) or sideways into the API Library (`run_library_api`). Every run writes an `agent_runs` row with tool-call trace + latency.
3. **Guardrails** — loop guard (force-escalate after >8 tool calls), per-customer rate limit (20/hr), consecutive-error limit (3), refund cap (₹10k on `issue_refund`).
4. **Fallback** — if `pydantic-ai` or `OPENAI_API_KEY` aren't available, a deterministic heuristic responder runs so offline demos still work. No network required.

The agent can be invoked three ways:

- **Automatic** — on every inbound, if the workspace has `ai_force_default` on or a published Langflow agent.
- **AI Draft chip** — agent runs once, drops output into the composer for the human to edit before sending.
- **Agent Builder → Test** — manual invocation against a seeded scenario.

---

## 6. Realtime — why two tabs stay in sync

Every page that shows live data subscribes to one or more WebSocket topics. When the server persists a change, it fans out an envelope to that topic and every subscribed tab patches its local state. No polling, no refetch.

| Page | Topics it subscribes to |
|---|---|
| Conversations | `inbox:<ws>` (thread list), `conv:<id>` (open thread) |
| Tickets | `inbox:<ws>` (pipeline), `ticket:<id>` (open ticket) |
| Teams | `teams:<channel_or_dm>`, `workspace:<ws>` (presence) |
| Notifications | `notif:<user>` |

Open two tabs, send from one — the other tab's inbox preview, chat pane, and underlying ticket timeline all update in <100 ms. Details in [wire.md §3](wire.md).

---

## 7. Customer-facing surface

The only customer-visible screen in this repo is the CSAT survey at [PublicCsat.jsx](src/pages/PublicCsat.jsx), reachable at `/csat/:token` without auth. The token is minted by `POST /tickets/:id/csat/send` and delivered out-of-band (email / WhatsApp template — Build 4). Rating + optional comment write back on `POST /csat/:token` and feed the `csat` analytics metric.

Every other customer touchpoint is a native channel (WhatsApp app, email client, Instagram DM, the embeddable web widget). They never see the workspace UI.

---

## 8. A day in the life

**09:00** — Priya (agent) signs in at [AuthPage](src/pages/AuthPage.jsx). Session token lands in `localStorage['workspacex.auth']`, WebSocket opens, `notif:priya` + `inbox:sleep-co` subscribe.

**09:02** — A WhatsApp message comes in: *"where's order #SC-3204?"*. The webhook hits `POST /conversations/:id/inbound`. The Stage-1 classifier matches `order #` → intent `order_status`, confidence 0.92, attaches to an existing open ticket. Priya sees a new preview slide to the top of her inbox.

**09:03** — The default orchestrator runs: `find_customer` by phone, `get_order("SC-3204")` returns `shipped, tracking TRK-991`, `send_reply` posts the reply. Priya sees the AI's message land in the chat pane. Because it was auto-resolvable, the ticket auto-transitions to `waiting_for_customer` and the AI deflection counter ticks up.

**09:30** — A harder one: *"the mattress arrived damaged"*. Stage-1 is ambiguous; Stage-2 LLM classifies as `damage_claim`, confidence 0.81, and creates a new ticket assigned to Priya. She gets a `notif:priya` push. The orchestrator takes a `kb_search` pass, decides it's above its refund cap, calls `escalate_to_human`, and stops. The ticket stays `assigned` to Priya.

**09:35** — Priya opens the thread. Right rail shows order + photos the customer attached. She types a reply; composer's AI Draft chip offers a template; she edits and sends. The ticket's timeline picks up `Priya replied on WhatsApp`.

**11:15** — Raj (lead) opens [Analytics](src/pages/Analytics.jsx). Today's bar chart shows 47 tickets, avg resolution 2.1h, CSAT 4.4, AI deflection 38%.

**13:00** — SLA worker notices a ticket assigned to Priya is 30 min from breach. Bell lights up, `notif:priya` gets an `sla` envelope, `inbox:sleep-co` gets `sla.breach_warning`.

**17:00** — Priya signs out. `closeRealtime()` drops the socket; `POST /auth/signout` kills the session row. Any tokens left behind can't be reused.

Every one of those events is a row in Postgres and a WS envelope. Nothing is magical; the wiring is in [wire.md](wire.md).
