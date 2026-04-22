# Progress

Last updated: 2026-04-22

## Done

### Design system
- Light theme with design tokens (purple primary `#7c3aed`, WhatsApp green, call orange, mail blue, Instagram pink).
- Typography, shadows, radii, channel-specific color palette all in [src/index.css](src/index.css).
- Layout primitives (icon rail, top bar, 3-pane split, right rail, right sidebar) in [src/App.css](src/App.css).

### Shared components
- [src/components/Icons.jsx](src/components/Icons.jsx) — 30+ inline SVG icons (Inbox, Ticket, Megaphone, Chart, Puzzle, Plug, Gear, Speaker, Bell, Headset, Search, Filter, Sort, Plus, Phone, WhatsApp, Mail, Instagram, Send, Clock, Image, Document, User, Expand, Pencil, Check2, Back, Menu, Info, Chevron).
- [src/components/Sidebar.jsx](src/components/Sidebar.jsx) — icon-only left rail with active-state highlight, matches the reference screenshots.
- [src/components/TopBar.jsx](src/components/TopBar.jsx) — shared top bar with bell (badge `6`), headset, workspace selector ("The Sleep Company").

### Pages
- **Conversations** ([src/pages/Conversations.jsx](src/pages/Conversations.jsx)) — 3-pane inbox:
  - Left: channel dropdown (All/WhatsApp/Instagram/Live Chat/Mails), search, tabs (All · Pending 263 · Unassigned 49), conversation list with channel-badge avatars, assignee chips, unread counters.
  - Center: chat head with Call + Reply split-button, dotted-grid chat background, incoming/outgoing bubbles with avatars + delivered ticks, system event lines ("Product Issue Report task started"), input with Reply button.
  - Right icon rail: Customer / Tickets / Send / Timeline / Media / Docs.
- **Tickets** ([src/pages/Tickets.jsx](src/pages/Tickets.jsx)) — 3-pane:
  - Left: "All Open" dropdown, search+sort, ticket cards with channel avatar, ID, customer, task tag, assignee, status badge (Assigned/Queued/Blocked) + SLA chip. Pagination `1-20 of 265`.
  - Center: Info / Conversation / Notes(1) / Customer tabs; Info panel with Customer card (avatar, phone, email, address, Orders/Returns stats), Timeline with connected dots, Order card with thumb/price.
  - Right sidebar: Status, Source, Task, Team, Order Status / Order Placed On, Pending Child Ticket (for blocked tickets, with prompt + "Choose an option"), assignee row with "No owner / Set Owner", Resolution/Execution tabs, VOC select, Save button.
- **Platforms** ([src/pages/Platforms.jsx](src/pages/Platforms.jsx)) — channel cards (WhatsApp, Email, Instagram, Web, Slack, Telegram, Messenger, Discord) + stats row (Connected, Tickets today, Pending setup, Avg response) + Connect/Manage toggle.
- **Agent Builder** ([src/pages/AgentBuilder.jsx](src/pages/AgentBuilder.jsx)) — agent config (name, model, persona, instructions, temperature slider) + tool toggles with descriptions.
- **Settings** ([src/pages/Settings.jsx](src/pages/Settings.jsx)) — Profile / Workspace / Notifications / Security / Billing tabs, each functional.
- **Analytics** ([src/pages/Analytics.jsx](src/pages/Analytics.jsx)) — stat cards (Tickets, Avg resolution, CSAT, AI deflection), chart placeholder.
- **Integrations** ([src/pages/Integrations.jsx](src/pages/Integrations.jsx)) — Shopify, Zendesk, Salesforce, Freshdesk, Zapier, Webhooks cards.

### Routing
- `/` → `/conversations` (default, matches inbox-first behaviour from screenshots).
- Routes wired for all pages in [src/App.jsx](src/App.jsx).

## Left (not implemented)

### Wiring / infra
- **Node.js is not installed on this machine.** Need to run `nvm install --lts` (or `apt install nodejs npm`) before `npm install && npm run dev`.
- No backend — all data is hardcoded in each page component.
- No API layer, no state management library (just `useState`).
- No auth / login flow.

### Polish and functionality missing vs screenshots
- **Conversations**: AI Draft Ready chip (shown in screenshot 5's mail view) + its email-thread reply mode not implemented — only chat-style reply is wired.
- **Conversations**: email thread UI (back button, subject line, "to Crm_Test" recipient picker, Reply at thread bottom) is not a separate view — all channels show the chat view.
- **Tickets**: filters beyond "All Open" are a stub (dropdown doesn't open). Sort icon is decorative.
- **Tickets**: New Ticket button is decorative (no create flow / modal).
- **Tickets**: Conversation, Notes, Customer tabs show a placeholder — only Info is populated.
- **Tickets**: VOC and Choose an option selects are decorative (no popover).
- **Pagination**: decorative — clicking pages doesn't slice data (only 4 sample tickets exist).
- **Agent Builder**: Save/Test buttons don't persist — purely in-memory.
- **Analytics**: chart area is a placeholder ("Charts coming soon"), no real graph library (e.g. recharts).
- **Integrations**: Configure/Connect buttons don't open anything.
- **Settings**: Save buttons don't POST anywhere.
- **Top bar**: Bell and Headset buttons are decorative (no popover/call drawer).
- **Sidebar**: Speaker/mute icon at the bottom is decorative.

### Nice-to-haves
- Mobile / responsive layout (current design assumes >= 1280px width).
- Dark-mode variant.
- Keyboard shortcuts (j/k to navigate conversations, etc.).
- Loading / empty / error states on each page.
- Unit or component tests.

## How to run

```bash
# Install Node (one time)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc && nvm install --lts

# Install deps and start dev server
cd ~/ticket
npm install
npm run dev
```

Then open http://localhost:5173 — you'll land on the Conversations inbox.
