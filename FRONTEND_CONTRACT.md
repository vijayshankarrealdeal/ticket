# Flowcall Frontend Contract

This document describes what the frontend currently renders, the shape of the
data it consumes, and the actions it performs. It is intended as a reference
for the FastAPI backend team so backend endpoints can be modelled to match the
frontend's expectations with minimal adapter code.

Today the frontend uses an in-memory mock dataset (see `src/pages/Conversations.jsx`
and `src/pages/Tickets.jsx`). Every piece of mock data listed here should map
1:1 to an API response when the backend is wired up.

---

## 1. Product / Workspace

- Product name is centralized in `src/config.js` (`PRODUCT_NAME = "Flowcall"`).
- The top-bar workspace label and the sidebar brand both read from this file.
- A future `GET /api/me` endpoint should return:
  ```json
  {
    "user": { "id": "u_1", "name": "Agent Name", "email": "agent@example.com" },
    "workspace": { "id": "ws_1", "name": "Flowcall" },
    "workspaces": [
      { "id": "ws_1", "name": "Flowcall" },
      { "id": "ws_2", "name": "The Sleep Company" }
    ]
  }
  ```

---

## 2. Routing & URL State

| Route | Purpose |
| --- | --- |
| `/` | Redirects to `/conversations`. |
| `/conversations` | Inbox (conversation list + thread view). |
| `/conversations?channel=<Channel>` | Filters the conversation list by channel. |
| `/tickets` | Ticket board. |
| `/platforms` | Broadcast platforms. |
| `/analytics` | Insights. |
| `/agent-builder` | Workflow / agent builder. |
| `/integrations` | Integrations. |
| `/settings` | Settings. |

### Channel values (URL `?channel=` and data)
`All` (no param), `WhatsApp`, `Instagram`, `Live Chat`, `Mails`.

> `All` is represented as the **absence** of the query parameter.

---

## 3. Data Models

### 3.1 Thread (a.k.a. Conversation)

```ts
type Thread = {
  id: number | string;
  name: string;                  // customer display name or phone
  isPhone?: boolean;             // true when `name` itself is a phone number
  channel: Channel;              // "WhatsApp" | "Instagram" | "Live Chat" | "Mails"
  date: string;                  // relative/short date shown in list, e.g. "Apr 22"
  preview: string;               // last message preview (truncated upstream)
  assignee?: string;             // short label like "Aniket ..", shown as chip
  unread?: number;               // unread count badge

  // Thread detail (may be lazy-loaded on selection):
  phone?: string;                // e.164 without '+', e.g. "918879317596"
  email?: string;
  orderId?: string;              // anchor order for the thread (shown above messages)
  messages?: Message[];
  tickets?: Ticket[];            // tickets linked to this conversation
  customer?: CustomerProfile;    // rich customer panel data
  notes?: Note[];                // private notes panel
  recordings?: Recording[];      // call recordings panel
  attachments?: Attachment[];    // media/attachments panel
};

type Note = {
  id: number | string;
  author: string;                // "You" for local, email/name otherwise
  at: string;                    // humanized timestamp
  text: string;
};

type Recording = {
  id: string;
  title?: string;                // falls back to id
  date: string;                  // humanized
  url?: string;                  // audio URL (optional, not rendered yet)
};

type Attachment = {
  id: string;                    // e.g. an order id acting as reference
  title?: string;                // optional display title
  date?: string;                 // humanized
  thumbUrl?: string;             // optional preview image
};

type Channel = "WhatsApp" | "Instagram" | "Live Chat" | "Mails";
```

### 3.2 Message

```ts
type Message =
  | {
      from: "them";
      at: string;                // human time e.g. "12:38 PM, 22 Apr"
      text: string;
      fromName?: string;         // shown for mail-style messages
      to?: string;               // e.g. "Crm_Test" (mail-style "to" label)
    }
  | {
      from: "me";
      at: string;
      text: string;
      delivered?: boolean;       // shows "Delivered ✓" marker
    }
  | {
      from: "system";
      at: string;
      text: string;              // renders as a centered system line
    };
```

### 3.3 Ticket

```ts
type Ticket = {
  id: string;                    // "#3759"
  status: "Assigned" | "Queued" | "Blocked" | "Resolved" | "Closed";
  statusClass?: "info" | "warning" | "danger" | "success" | "primary";
  source: Channel;
  task: string;                  // machine task id like "delay_delivery"
  team: string;                  // "CRM L1", "Logistics", ...
  orderPlacedOn?: string;        // "2 April 2026"
  orderStatus?: string;          // shown when orderPlacedOn missing
  pendingChild?: PendingChildTicket;
  assignee?: string;
  sla?: string;                  // "11d 4h"
  timeline?: TimelineEntry[];
  customerDetails?: CustomerProfile;
  order?: OrderSummary;
};

type PendingChildTicket = {
  assigneeEmail: string;         // "aftab.sayyed@thesleepcompany.in"
  team: string;                  // "Logistics"
  objective: string;             // machine id shown as label, e.g. "logistics_status"
  prompt: string;                // human instruction for the agent
  resolution?: string;           // comma separated option list shown as hint
};

type TimelineEntry = { at: string; text: string };
```

### 3.4 Customer profile (shown in the Customer panel)

```ts
type CustomerProfile = {
  name: string;
  phone?: string;                // e.164 no '+' ("918898400709")
  email?: string;
  since?: string;                // "Mar 2026"
  address?: string;              // primary address, single line with commas
  otherAddresses?: string[];     // secondary addresses
  orders: number;                // lifetime order count
  returns: number;               // lifetime return count
  recentOrders?: RecentOrder[];
};

type RecentOrder = {
  id: string;                    // "20640127"
  time: string;                  // "6:00 PM, Mar 9, 26"
  status?: string;               // "Cancellation Rejected on 11 April 2026"
  statusTone?: "success" | "info" | "warning" | "danger";
  total: number;                 // major units (INR)
  itemsCount: number;
  items: { name: string; qty: number; price: number }[];
};

type OrderSummary = {
  id: string;                    // "20466915" (no leading #)
  price: number;
  item: string;
  qty: number;
  thumb?: string;                // emoji or image url
};
```

---

## 4. Pages & Panels

### 4.1 Sidebar (`src/components/Sidebar.jsx`)

- Collapsible rail. Persisted in `localStorage["flowcall-sidebar-expanded"]`.
- Groups: **Inbox, Tickets, Broadcasts, Insights, Workflow, Integrations, Settings**.
- `Inbox` expands to show channel sub-items (`All`, `Whatsapp`, `Instagram`, `Live Chat`, `Mails`). Clicking a sub-item navigates to `/conversations?channel=<Key>`.
- Bottom has theme toggle (`light`/`dark`) and a Sound placeholder.
- Theme persisted in `localStorage["flowcall-theme"]`. Dark is the default.

### 4.2 Inbox / Conversations (`src/pages/Conversations.jsx`)

Layout (left → right):

1. **Conversation list pane** (360px)
   - Search input, filter button.
   - Tabs: `All`, `Pending (count)`, `Unassigned (count)`.
   - Each row shows avatar (with small channel glyph), name, short date, preview, optional assignee chip, optional unread badge.
2. **Chat center pane**
   - Chat head: customer name + phone/email + three circular action buttons (WhatsApp / Email / Call).
   - Optional order bar beneath showing the anchored order id (`thread.orderId`).
   - Chat body:
     - WhatsApp/Live Chat → bubble style.
     - Mail threads → full mail card (`fromName`, `to`, subject/body).
     - `from: "system"` → dashed divider with icon.
   - Composer: text input, AI draft chip, Reply split button.
3. **Right context panel** (one of, toggled from the far-right rail)
   - **Customer Information** (User icon): profile header, Orders/Returns stat cards, Primary Address, Other Addresses, Recent Orders accordion.
   - **Tickets** (Ticket icon, default open): ticket status/source/task/team chips, Order Placed On, Pending Child Ticket card (assignee, team, objective, prompt, resolution hint).
   - **Recordings** (paper-plane icon): list of call recordings with refresh, empty state "No call recordings found".
   - **Attachments** (image icon): grid of media/attachment cards (e.g. order thumbnails), empty state "No attachments found".
   - **Notes** (document icon): Private Notes list + inline "Add New Note" textarea and button; empty state "No notes yet".
4. **Right rail** (56px, icon only): Customer, Tickets (with count badge), Recordings, Timeline, Attachments, Notes.

### 4.3 Tickets (`src/pages/Tickets.jsx`)

- Left: paginated ticket list with status/SLA chips.
- Center: tabs `Info | Conversation | Notes | Customer`. The Info tab renders Customer panel, Timeline, and Order card.
- Right: ticket sidebar with Status/Source/Task, Team, Order Placed On or Order Status, Pending Child Ticket form, Resolution/Execution tabs, VOC selector, Save action.

### 4.4 Other pages

`Platforms`, `Analytics`, `AgentBuilder`, `Integrations`, `Settings` are scaffolds with static content today. They do not yet consume backend data.

---

## 5. Frontend Actions (need backend endpoints)

| User action | Current FE behavior | Expected backend endpoint |
| --- | --- | --- |
| Open inbox | Renders threads, filters by channel | `GET /api/threads?channel=<Channel>&tab=<all\|pending\|unassigned>` |
| Open thread | Renders messages + tickets + customer | `GET /api/threads/{id}` |
| Reply in thread | Appends optimistic `{from:"me", text, delivered:true}` | `POST /api/threads/{id}/messages` body `{ text: string }` |
| Filter by channel | URL updates, list refetches | same as inbox list with `channel` param |
| Toggle ticket panel | Uses `thread.tickets[]` | included in `GET /api/threads/{id}` |
| Toggle customer panel | Uses `thread.customer` | included in `GET /api/threads/{id}` (or `GET /api/customers/{id}`) |
| Open Notes panel | Uses `thread.notes`, adds optimistic note on submit | `GET /api/threads/{id}/notes`, `POST /api/threads/{id}/notes` body `{ text }` |
| Open Recordings panel | Uses `thread.recordings` | `GET /api/threads/{id}/recordings` |
| Open Attachments panel | Uses `thread.attachments` | `GET /api/threads/{id}/attachments` |
| Tickets page list | Renders paginated tickets | `GET /api/tickets?status=<>&page=<>&perPage=20` |
| Open ticket | Renders detail + order + timeline | `GET /api/tickets/{id}` |
| Child ticket action | Resolution dropdown (no handler yet) | `POST /api/tickets/{id}/resolve` body `{ objective, value, note? }` |

### Suggested response envelope

```json
{ "data": {...}, "meta": { "page": 1, "total": 265 } }
```

### Suggested list response for threads

```json
{
  "data": [
    {
      "id": 1,
      "name": "+91 88 7931 7596",
      "isPhone": true,
      "channel": "WhatsApp",
      "date": "Apr 22",
      "preview": "Hey! I'd love t...",
      "unread": 0
    }
  ],
  "meta": { "counts": { "all": 312, "pending": 263, "unassigned": 49 } }
}
```

---

## 6. Enum Vocabulary

The FE renders these strings directly — backends should emit them verbatim.

- **Channel:** `WhatsApp`, `Instagram`, `Live Chat`, `Mails`.
- **Ticket status:** `Assigned`, `Queued`, `Blocked`, `Resolved`, `Closed`.
- **Ticket source:** same as Channel (`WhatsApp`, `Call`, `Mails`, ...).
- **Tone classes** used for chip colors: `info`, `primary`, `success`, `warning`, `danger`, `whatsapp`.

---

## 7. Presentation Rules the Backend Should Know

- `phone` is stored and displayed without `+`. The FE prefixes the call/whatsapp actions itself.
- `date` on a thread is already a humanized string (`Apr 22`). If the backend returns ISO timestamps instead, an adapter must humanize.
- `preview` is expected to already be truncated (with `...`).
- `customer.recentOrders[].total` and `items[].price` are integers in the major unit (₹). No currency prefix in the data.
- `orderId` on a thread is the string shown in the order bar above the chat.
- Avatars are colored deterministically from the name on the client — backend does not need to send a color.

---

## 8. Settings Persisted Client-Side

| Key | Values | Meaning |
| --- | --- | --- |
| `flowcall-theme` | `"dark"` (default) \| `"light"` | Color theme |
| `flowcall-sidebar-expanded` | `"true"` (default) \| `"false"` | Left rail expanded |

These are purely UI state and do not need to round-trip through the server.

---

## 9. Files to Read First

- `src/pages/Conversations.jsx` — thread/message/ticket/customer shape in use.
- `src/pages/Tickets.jsx` — ticket list + detail shape in use.
- `src/components/Sidebar.jsx` — channel keys and nav groups.
- `src/config.js` — product/workspace label.
- `src/App.css` & `src/index.css` — theming contract (CSS variables); backend does not need to touch these.
