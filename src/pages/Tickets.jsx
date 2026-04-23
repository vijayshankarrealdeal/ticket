import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { useGlobalSearch } from '../hooks/useGlobalSearch.jsx';
import CreateTicketModal from '../components/CreateTicketModal.jsx';
import { api } from '../api.js';
import { useRealtimeTopic } from '../hooks/useRealtime.js';
import { loadAuth } from './AuthPage.jsx';

const SORT_OPTIONS = [
  { key: 'newest', label: 'Newest first' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'sla-asc', label: 'SLA: least time left' },
  { key: 'sla-desc', label: 'SLA: most time left' },
  { key: 'customer', label: 'Customer A–Z' },
];

const FILTERS = [
  { key: 'all-open', label: 'All Open', match: (t) => t.status !== 'resolved' && t.status !== 'closed' },
  { key: 'resolved', label: 'Resolved', match: (t) => t.status === 'resolved' || t.status === 'closed' },
  { key: 'all', label: 'All', match: () => true },
  { key: 'my-tickets', label: 'My Tickets', match: (t) => t.assignee === 'Aniket Singh' },
  { key: 'unassigned', label: 'Unassigned', match: (t) => !t.assignee },
  { key: 'blocked', label: 'Blocked', match: (t) => t.status === 'blocked' },
  { key: 'my-created-progress', label: 'My Created · In Progress', match: (t) => t.status === 'assigned' },
];

const slaHours = (sla) => {
  if (!sla) return Number.POSITIVE_INFINITY;
  const d = sla.match(/(\d+)d/);
  const h = sla.match(/(\d+)h/);
  return (d ? parseInt(d[1], 10) : 0) * 24 + (h ? parseInt(h[1], 10) : 0);
};


const statusMeta = {
  assigned: { label: 'Assigned', tone: 'info' },
  queued:   { label: 'Queued',   tone: 'warning' },
  blocked:  { label: 'Blocked',  tone: 'danger' },
  resolved: { label: 'Resolved', tone: 'success' },
  closed:   { label: 'Closed',   tone: 'muted' },
};

const channelAvatar = (channel) => {
  if (channel === 'WhatsApp') return { bg: '#22c55e', icon: <Icon.WhatsApp /> };
  if (channel === 'Call')     return { bg: '#f97316', icon: <Icon.Phone style={{ width: 14, height: 14 }} /> };
  if (channel === 'Mail')     return { bg: '#3b82f6', icon: <Icon.Mail style={{ width: 14, height: 14 }} /> };
  return { bg: '#1e40af', icon: <Icon.Inbox style={{ width: 14, height: 14 }} /> };
};

export default function Tickets() {
  const [params, setParams] = useSearchParams();
  const [tickets, setTickets] = useState([]);
  const [selectedId, setSelectedId] = useState(() => {
    const raw = params.get('id');
    if (!raw) return null;
    return raw.startsWith('#') ? raw : `#${raw}`;
  });

  useEffect(() => {
    let alive = true;
    api.get('/tickets')
      .then((res) => {
        if (!alive) return;
        const items = Array.isArray(res) ? res : res?.items || [];
        // Preserve detail fields (customerDetails, timeline, notes, order,
        // …) that the list endpoint doesn't return, so a refetch doesn't
        // wipe the currently-open ticket's cached detail.
        setTickets((prev) => items.map((it) => {
          const cached = prev.find((t) => t.id === it.id);
          return cached ? { ...cached, ...it } : it;
        }));
        setSelectedId((cur) => cur ?? items[0]?.id ?? null);
      })
      .catch(() => { if (alive) setTickets([]); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    api.get(`/tickets/${encodeURIComponent(selectedId)}`)
      .then((detail) => {
        if (!alive || !detail) return;
        setTickets((prev) => {
          // If the list hasn't landed yet (or this ticket was pruned by
          // a filter), still cache the detail by inserting it — otherwise
          // the merge no-ops against an empty/stale list and the detail
          // is quietly dropped.
          const idx = prev.findIndex((t) => t.id === selectedId);
          if (idx === -1) return [...prev, { ...detail, id: selectedId }];
          return prev.map((t, i) => i === idx ? { ...t, ...detail, id: t.id } : t);
        });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [selectedId]);
  const [tab, setTab] = useState('info');
  const { query: search } = useGlobalSearch();
  const [sort, setSort] = useState('newest');
  const [sortOpen, setSortOpen] = useState(false);
  const [filterKey, setFilterKey] = useState('all-open');
  const [filterOpen, setFilterOpen] = useState(false);
  const [expandedOpen, setExpandedOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem('tickets.sidebarCollapsed') === '1'; }
    catch { return false; }
  });
  const filterRef = useRef(null);
  const sortRef = useRef(null);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    try { localStorage.setItem('tickets.sidebarCollapsed', sidebarCollapsed ? '1' : '0'); }
    catch { /* noop */ }
  }, [sidebarCollapsed]);

  useEffect(() => {
    const raw = params.get('id');
    if (!raw) return;
    const normalized = raw.startsWith('#') ? raw : `#${raw}`;
    if (tickets.find((t) => t.id === normalized)) {
      setSelectedId(normalized);
    }
    const next = new URLSearchParams(params);
    next.delete('id');
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  // Realtime — push updates for the inbox (list) + the open ticket.
  //
  // Publishers aren't consistent about `evt.data.id`: the PATCH/resolve
  // routes send the serialized ticket (id = "#9", string) while the
  // orchestrator + worker + merge endpoint send raw DB ids (id = 9, int).
  // Pin `t.id` across the spread so an int payload can never clobber the
  // canonical "#"-prefixed id — otherwise the next
  // `tickets.find(t => t.id === selectedId)` returns undefined and the
  // whole detail pane goes blank until a full refetch.
  const normalizeTicketId = (raw) => {
    if (raw === null || raw === undefined) return null;
    const s = String(raw);
    return s.startsWith('#') ? s : `#${s}`;
  };
  const workspaceId = loadAuth()?.workspaceId;
  useRealtimeTopic(workspaceId ? `inbox:${workspaceId}` : null, (evt) => {
    if (evt.type === 'ticket.updated') {
      const eid = normalizeTicketId(evt.data?.id);
      if (!eid) return;
      setTickets((prev) => prev.map((t) => t.id === eid ? { ...t, ...evt.data, id: t.id } : t));
    } else if (evt.type === 'ticket.created') {
      const eid = normalizeTicketId(evt.data?.id);
      if (!eid) return;
      setTickets((prev) => prev.some((t) => t.id === eid) ? prev : [{ ...evt.data, id: eid }, ...prev]);
    }
  });
  useRealtimeTopic(
    selectedId ? `ticket:${selectedId.replace(/^#/, '')}` : null,
    (evt) => {
      if (evt.type === 'ticket.updated') {
        setTickets((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...evt.data, id: t.id } : t));
      } else if (evt.type === 'ticket.timeline.appended') {
        // refetch the detail to pick up the appended timeline entry
        api.get(`/tickets/${encodeURIComponent(selectedId)}`).then((detail) => {
          if (!detail) return;
          setTickets((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...detail, id: t.id } : t));
        }).catch(() => {});
      }
    }
  );

  const selected = tickets.find((t) => t.id === selectedId);

  // close dropdowns on outside click
  useEffect(() => {
    function onClick(e) {
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false);
      if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  // close modal on Escape + lock body scroll while open
  useEffect(() => {
    if (!expandedOpen) return;
    function onKey(e) { if (e.key === 'Escape') setExpandedOpen(false); }
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [expandedOpen]);

  const filterCounts = useMemo(() => {
    const out = {};
    for (const f of FILTERS) out[f.key] = tickets.filter(f.match).length;
    return out;
  }, [tickets]);

  const visibleTickets = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filterFn = FILTERS.find((f) => f.key === filterKey)?.match || (() => true);
    let out = tickets.filter(filterFn).filter((t) => {
      if (!q) return true;
      const id = String(t.id || '').toLowerCase();
      const cust = String(t.customer || '').toLowerCase();
      const task = String(t.task || '').toLowerCase();
      const assignee = String(t.assignee || '').toLowerCase();
      return id.includes(q) || cust.includes(q) || task.includes(q) || assignee.includes(q);
    });
    const idNum = (t) => parseInt(String(t.id || '').replace(/\D/g, ''), 10) || 0;
    switch (sort) {
      case 'oldest':    out = [...out].sort((a, b) => idNum(a) - idNum(b)); break;
      case 'sla-asc':   out = [...out].sort((a, b) => slaHours(a.sla) - slaHours(b.sla)); break;
      case 'sla-desc':  out = [...out].sort((a, b) => slaHours(b.sla) - slaHours(a.sla)); break;
      case 'customer':  out = [...out].sort((a, b) => String(a.customer || '').localeCompare(String(b.customer || ''))); break;
      default:          out = [...out].sort((a, b) => idNum(b) - idNum(a));
    }
    return out;
  }, [search, sort, filterKey, tickets]);

  const filterLabel = FILTERS.find((f) => f.key === filterKey)?.label;
  const sortLabel = SORT_OPTIONS.find((o) => o.key === sort)?.label;

  return (
    <>
      <TopBar
        left={
          <>
            <div className="top-bar-title">Tickets</div>
            <button
              className="btn"
              style={{ marginLeft: 10 }}
              onClick={() => setCreateOpen(true)}
            >
              <Icon.Plus /> New Ticket
            </button>
          </>
        }
      />
      <div className="main">
        <div className="pane">
          {/* Ticket list */}
          <div className="left-pane tickets-left">
            <div className="tickets-list-header">
              <div className="dropdown" ref={filterRef} style={{ width: '100%' }}>
                <button
                  className={'select-input filter-select' + (filterOpen ? ' open' : '')}
                  onClick={() => setFilterOpen((v) => !v)}
                >
                  <span className="filter-select-label">{filterLabel}</span>
                  <span className="filter-select-count">{filterCounts[filterKey] || 0}</span>
                  <Icon.Chevron className={'caret' + (filterOpen ? ' up' : '')} />
                </button>
                {filterOpen && (
                  <div className="dropdown-menu filter-menu" style={{ width: '100%' }}>
                    <div className="filter-menu-title">Show</div>
                    {FILTERS.map((f) => (
                      <button
                        key={f.key}
                        className={'filter-option button-row' + (filterKey === f.key ? ' active' : '')}
                        onClick={() => { setFilterKey(f.key); setFilterOpen(false); }}
                      >
                        <span style={{ flex: 1 }}>{f.label}</span>
                        <span className="filter-count-pill">{filterCounts[f.key] || 0}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="list-toolbar">
                <div className="list-toolbar-hint">
                  {search.trim() ? (
                    <>Filtering by <b>"{search}"</b></>
                  ) : (
                    <>{visibleTickets.length} {visibleTickets.length === 1 ? 'ticket' : 'tickets'}</>
                  )}
                </div>
                <div className="dropdown" ref={sortRef} style={{ display: 'flex' }}>
                  <button
                    className={'search-icon-btn boxed' + (sortOpen || sort !== 'newest' ? ' active' : '')}
                    title={`Sort: ${sortLabel}`}
                    onClick={() => setSortOpen((v) => !v)}
                  >
                    <Icon.Sort />
                  </button>
                  {sortOpen && (
                    <div className="dropdown-menu sort-menu" style={{ right: 0, left: 'auto' }}>
                      <div className="filter-menu-title">Sort by</div>
                      {SORT_OPTIONS.map((o) => (
                        <button
                          key={o.key}
                          className={'filter-option button-row' + (sort === o.key ? ' active' : '')}
                          onClick={() => { setSort(o.key); setSortOpen(false); }}
                        >
                          {sort === o.key ? <Icon.Check2 /> : <span style={{ width: 14, flex: 'none' }} />}
                          <span>{o.label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="conv-list">
              {visibleTickets.map((t) => (
                <TicketCard key={t.id} t={t} selected={t.id === selectedId} onClick={() => setSelectedId(t.id)} />
              ))}
              {visibleTickets.length === 0 && (
                <div className="tickets-empty">
                  <Icon.InboxEmpty />
                  <div>No tickets match your search.</div>
                </div>
              )}
            </div>
          </div>

          {/* Ticket detail */}
          <div className="center-pane">
            {selected && (
              <DetailHeader
                t={selected}
                sidebarCollapsed={sidebarCollapsed}
                onShowSidebar={() => setSidebarCollapsed(false)}
              />
            )}
            <div className="detail-tabs">
              {[
                { k: 'info', label: 'Info' },
                { k: 'convo', label: 'Conversation', icon: <Icon.Inbox /> },
                { k: 'notes', label: 'Notes', pill: selected?.notes?.length || 0 },
                { k: 'customer', label: 'Customer', icon: <Icon.User /> },
              ].map((x) => (
                <button
                  key={x.k}
                  className={'detail-tab' + (tab === x.k ? ' active' : '')}
                  onClick={() => setTab(x.k)}
                >
                  {x.icon}
                  {x.label}
                  {x.pill ? <span className="pill">{x.pill}</span> : null}
                </button>
              ))}
            </div>

            {tab === 'info' && selected && <InfoBody t={selected} />}
            {tab === 'convo' && selected && <ConvoBody t={selected} />}
            {tab === 'notes' && selected && <NotesBody t={selected} />}
            {tab === 'customer' && selected && <CustomerBody t={selected} /> }
          </div>

          {/* Right sidebar */}
          {selected && !sidebarCollapsed && (
            <TicketSidebar
              t={selected}
              onExpand={() => setExpandedOpen(true)}
              onCollapse={() => setSidebarCollapsed(true)}
              onPatch={(patch) =>
                setTickets((prev) => prev.map((x) => x.id === selected.id ? { ...x, ...patch } : x))
              }
            />
          )}
        </div>
      </div>

      {expandedOpen && selected && (
        <TicketDetailModal t={selected} onClose={() => setExpandedOpen(false)} />
      )}

      {createOpen && (
        <CreateTicketModal
          onClose={() => setCreateOpen(false)}
          onCreated={() => { /* mock: no-op */ }}
        />
      )}
    </>
  );
}

function TicketCard({ t, selected, onClick }) {
  const ch = channelAvatar(t.channel);
  const meta = statusMeta[t.status];
  return (
    <div className={'ticket-card' + (selected ? ' selected' : '')} onClick={onClick}>
      <div className="ticket-card-top">
        <div className="avatar sm" style={{ background: ch.bg }}>{ch.icon}</div>
        <div className="ticket-card-headline">
          <span className="ticket-id">{t.id}</span>
          <span className="ticket-dot-sep">·</span>
          <span className="ticket-customer">{t.customer}</span>
        </div>
        <span className="ticket-date">{t.ago}</span>
      </div>

      <div className="ticket-task-row">
        <Icon.Puzzle style={{ width: 12, height: 12, color: 'var(--muted)' }} />
        <span className="ticket-task-name">{t.task}</span>
      </div>

      <div className="ticket-card-foot">
        <span className={'status-pill tone-' + meta.tone}>
          <span className="status-dot" />
          {meta.label}
        </span>
        <span className="sla-chip">
          <Icon.Clock style={{ width: 11, height: 11 }} /> {t.sla}
        </span>
        {t.assignee && (
          <span className="assignee-mini" title={t.assignee}>
            <span className="mini-avatar">
              {t.assignee.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

function DetailHeader({ t, sidebarCollapsed, onShowSidebar }) {
  const meta = statusMeta[t.status];
  return (
    <div className="detail-header">
      <div className="detail-header-left">
        <div className="detail-id">{t.id}</div>
        <span className={'status-pill tone-' + meta.tone}>
          <span className="status-dot" />
          {meta.label}
        </span>
        <span className="detail-header-task">{t.task}</span>
      </div>
      <div className="detail-header-right">
        <button className="icon-btn" title="Copy link"><Icon.Paperclip /></button>
        <button className="icon-btn" title="Refresh"><Icon.Refresh /></button>
        <button className="icon-btn" title="More"><Icon.More /></button>
        {sidebarCollapsed && (
          <button
            className="icon-btn side-toggle-btn"
            title="Show details panel"
            onClick={onShowSidebar}
          >
            <Icon.SidebarToggle />
          </button>
        )}
      </div>
    </div>
  );
}

function InfoBody({ t }) {
  // Defensive: the list endpoint only returns base fields — `customerDetails`,
  // `timeline`, and `order` arrive via the detail fetch. Before that fetch
  // lands (and for tickets that simply have no order), those keys are absent,
  // so every access has to be guarded or the whole Tickets page blanks out.
  const customerName = t.customer || 'Unknown';
  const cd = t.customerDetails || {};
  const timeline = t.timeline || [];
  const order = t.order || null;
  const navigate = useNavigate();
  const gotoCustomer = () => {
    const qp = new URLSearchParams();
    if (t.customerId) qp.set('id', t.customerId);
    else if (cd.phone) qp.set('phone', cd.phone);
    else if (cd.email) qp.set('email', cd.email);
    else qp.set('name', customerName);
    navigate('/customers' + (qp.toString() ? `?${qp}` : ''));
  };
  return (
    <div className="detail-body">
      {/* Customer */}
      <div className="panel">
        <div className="panel-head">
          <span>Customer</span>
          <button className="link link-btn" onClick={gotoCustomer}>View details <Icon.Chevron style={{ transform: 'rotate(-90deg)', width: 12, height: 12 }} /></button>
        </div>
        <div className="customer-top-row">
          <div className="avatar" style={{ background: '#1e40af' }}>
            {customerName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="customer-top-name">{customerName}</div>
            <div className="customer-top-sub">Customer since {cd.since || '—'}</div>
          </div>
        </div>
        <div className="kv-list">
          <div className="kv-row"><Icon.Phone className="icon" /><span>{cd.phone || '—'}</span></div>
          <div className="kv-row"><Icon.Mail className="icon" /><span>{cd.email || '—'}</span></div>
          <div className="kv-row" style={{ alignItems: 'flex-start' }}>
            <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
            <span>{cd.address || '—'}</span>
          </div>
        </div>
        <div className="customer-metric-row">
          <div className="customer-metric">
            <div className="metric-value">{cd.orders ?? 0}</div>
            <div className="metric-label">Orders</div>
          </div>
          <div className="customer-metric">
            <div className="metric-value">{cd.returns ?? 0}</div>
            <div className="metric-label">Returns</div>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="panel">
        <div className="panel-head"><span>Activity</span></div>
        <div className="timeline">
          {timeline.length === 0 ? (
            <div style={{ color: 'var(--muted)', fontSize: 13 }}>No activity yet.</div>
          ) : timeline.map((e, i) => (
            <div key={i} className="tl-item">
              <div>
                <div className="txt">{e.text}</div>
                <div className="sub">{e.at}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Order spans full width — omit if this ticket has no linked order */}
      {order && (
        <div className="panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-head">
            <span>Order</span>
            <span className="muted-small">Ordered {order.placedOn || 'Apr 22, 2026'}</span>
          </div>
          <div className="order-card">
            <div className="order-card-top">
              <div>
                <div className="order-id">#{order.id}</div>
                <div className="order-sub">
                  <span className="muted-dot success" /> Paid
                  <span className="order-sub-sep">·</span>
                  {t.orderStatus || 'Processing'}
                </div>
              </div>
              <div className="order-total">₹{Number(order.price || 0).toLocaleString()}</div>
            </div>
            {(order.items || []).map((it, i) => (
              <div key={i} className="order-item">
                <div className="order-thumb"><Icon.ShoppingBag /></div>
                <div style={{ minWidth: 0 }}>
                  <div className="order-item-name">{it.name}</div>
                  <div className="order-item-qty">SKU: {it.sku} · Qty: {it.qty}</div>
                </div>
                <div className="order-item-price">₹{Number((it.price || 0) * (it.qty || 1)).toLocaleString()}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EmptyBody({ label }) {
  return (
    <div className="empty-state" style={{ margin: 'auto' }}>
      <Icon.InboxEmpty />
      <div className="empty-state-text">{label} coming soon.</div>
    </div>
  );
}

// Gmail/Outlook top-post a reply with the prior thread quoted below an
// "On <date>, <addr> wrote:" marker — sometimes lines get `> ` prefixes,
// sometimes long quoted lines get soft-wrapped and lose the prefix on
// continuations. Cut at the marker and drop any `>` lines that remain,
// then trim so the bubble shows just what the sender actually wrote.
function cleanEmailReply(raw) {
  if (!raw) return '';
  const text = String(raw).replace(/\r\n/g, '\n');
  const marker = /\n\s*On\s[\s\S]*?wrote:\s*\n?/i;
  const m = text.match(marker);
  let head = m ? text.slice(0, m.index) : text;
  head = head.replace(/\n--\s*\n[\s\S]*$/, ''); // RFC 3676 signature
  head = head.split('\n').filter((l) => !/^\s*>/.test(l)).join('\n');
  return head.replace(/\n{3,}/g, '\n\n').trim();
}

function prettyFromName(name) {
  if (!name) return '';
  if (name === 'default-orchestrator') return 'AI Agent';
  return name;
}

function ConvoBody({ t }) {
  // Shows the conversation linked to this ticket. The server already carries
  // conversation id on the ticket row (ticket.conversationId when set) — we
  // fetch lazily so the tab loads only when opened.
  const [messages, setMessages] = useState(null);
  const convId = t?.conversationId || t?.conversation_id;
  useEffect(() => {
    if (!convId) { setMessages([]); return; }
    let alive = true;
    api.get(`/conversations/${convId}`)
      .then((c) => { if (alive) setMessages(Array.isArray(c?.messages) ? c.messages : []); })
      .catch(() => { if (alive) setMessages([]); });
    return () => { alive = false; };
  }, [convId]);
  if (!convId) {
    return <div className="empty-state" style={{ margin: 'auto' }}><Icon.InboxEmpty /><div className="empty-state-text">No conversation linked.</div></div>;
  }
  if (messages === null) {
    return <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>;
  }
  if (messages.length === 0) {
    return <div className="empty-state" style={{ margin: 'auto' }}><Icon.InboxEmpty /><div className="empty-state-text">No messages yet.</div></div>;
  }
  return (
    <div style={{ padding: 16, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {messages.map((m, i) => {
        const mine = m.from === 'me' || m.from === 'agent';
        const clean = cleanEmailReply(m.text);
        const original = String(m.text || '').trim();
        const hasQuoted = clean && original && clean.length < original.length;
        return (
          <MessageBubble
            key={m.id || i}
            mine={mine}
            fromName={prettyFromName(m.fromName)}
            text={clean || original}
            quoted={hasQuoted ? original : null}
            at={m.at}
          />
        );
      })}
    </div>
  );
}

function MessageBubble({ mine, fromName, text, quoted, at }) {
  const [showQuoted, setShowQuoted] = useState(false);
  return (
    <div style={{
      alignSelf: mine ? 'flex-end' : 'flex-start',
      maxWidth: '72%',
      background: mine ? 'var(--primary, #1e40af)' : 'var(--hover)',
      color: mine ? '#fff' : 'var(--text)',
      padding: '10px 14px',
      borderRadius: 14,
      fontSize: 14,
      lineHeight: 1.5,
      boxShadow: '0 1px 2px rgba(0,0,0,0.15)',
    }}>
      {fromName && (
        <div style={{ fontSize: 11, fontWeight: 600, opacity: 0.8, marginBottom: 4 }}>
          {fromName}
        </div>
      )}
      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</div>
      {quoted && (
        <>
          <button
            onClick={() => setShowQuoted((v) => !v)}
            style={{
              marginTop: 6, padding: 0, background: 'transparent', border: 'none',
              color: mine ? 'rgba(255,255,255,0.75)' : 'var(--muted)',
              fontSize: 11, cursor: 'pointer', textDecoration: 'underline',
            }}
          >
            {showQuoted ? 'Hide quoted text' : 'Show quoted text'}
          </button>
          {showQuoted && (
            <div style={{
              marginTop: 6, padding: '8px 10px', borderLeft: '2px solid currentColor',
              opacity: 0.7, fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {quoted}
            </div>
          )}
        </>
      )}
      {at && (
        <div style={{ fontSize: 10, opacity: 0.65, marginTop: 6, textAlign: mine ? 'right' : 'left' }}>
          {at}
        </div>
      )}
    </div>
  );
}

function NotesBody({ t }) {
  const [draft, setDraft] = useState('');
  const [notes, setNotes] = useState(Array.isArray(t?.notes) ? t.notes : []);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setNotes(Array.isArray(t?.notes) ? t.notes : []);
  }, [t?.id, t?.notes]);
  async function submit(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setBusy(true);
    try {
      const created = await api.post(`/tickets/${t.idNumber || t.id}/notes`, { text });
      setNotes((prev) => [...prev, created]);
      setDraft('');
    } finally { setBusy(false); }
  }
  return (
    <div style={{ padding: 16, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
      {notes.length === 0 && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No notes yet.</div>}
      {notes.map((n, i) => (
        <div key={i} style={{ background: 'var(--hover)', padding: 12, borderRadius: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
            {n.author || 'Unknown'} · {n.at}
          </div>
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>{n.text}</div>
        </div>
      ))}
      <form onSubmit={submit} style={{ display: 'flex', gap: 8, marginTop: 'auto' }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add an internal note…"
          style={{ flex: 1 }}
        />
        <button className="btn" disabled={busy || !draft.trim()}>{busy ? 'Saving…' : 'Add note'}</button>
      </form>
    </div>
  );
}

function CustomerBody({ t }) {
  const cd = t?.customerDetails || {};
  const rows = [
    ['Name', t?.customer],
    ['Email', cd.email],
    ['Phone', cd.phone],
    ['Customer since', cd.since],
    ['Address', cd.address],
    ['Orders', cd.orders],
    ['Returns', cd.returns],
  ];
  return (
    <div style={{ padding: 20, overflow: 'auto' }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>Customer profile</div>
      <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', rowGap: 8, columnGap: 16, fontSize: 14 }}>
        {rows.map(([k, v]) => (
          <Fragment key={k}>
            <div style={{ color: 'var(--muted)' }}>{k}</div>
            <div>{v ?? '—'}</div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function PropRow({ label, children }) {
  return (
    <div className="prop-row">
      <div className="prop-label">{label}</div>
      <div className="prop-value">{children}</div>
    </div>
  );
}

function TicketSidebar({ t, onExpand, onCollapse, onPatch }) {
  const [resolTab, setResolTab] = useState('resolution');
  const [busy, setBusy] = useState(null);
  const [flash, setFlash] = useState(null);
  const [modal, setModal] = useState(null); // 'merge'|'split'|'attach'|null
  const meta = statusMeta[t.status] || { label: t.status || 'Queued', tone: 'info' };

  const flashOk = (msg) => { setFlash({ ok: true, msg }); setTimeout(() => setFlash(null), 2500); };
  const flashErr = (msg) => { setFlash({ ok: false, msg }); setTimeout(() => setFlash(null), 3500); };

  async function submitMerge(intoTicketId) {
    setBusy('merge');
    try {
      await api.post(`/tickets/${encodeURIComponent(t.id)}/merge`, {
        intoTicketId: String(intoTicketId).replace(/^#/, ''),
      });
      flashOk(`Merged into #${String(intoTicketId).replace(/^#/, '')}`);
      setModal(null);
    } catch (e) {
      flashErr(e?.message || 'merge failed');
    } finally {
      setBusy(null);
    }
  }

  async function submitSplit(fromMessageId) {
    setBusy('split');
    try {
      const res = await api.post(`/tickets/${encodeURIComponent(t.id)}/split`, {
        fromMessageId,
      });
      flashOk(`New ticket #${res.newTicketId || res.id || ''}`);
      setModal(null);
    } catch (e) {
      flashErr(e?.message || 'split failed');
    } finally {
      setBusy(null);
    }
  }

  async function submitAttach(messageId) {
    setBusy('attach');
    try {
      await api.post(`/tickets/${encodeURIComponent(t.id)}/attach-message`, { messageId });
      flashOk('Message attached');
      setModal(null);
    } catch (e) {
      flashErr(e?.message || 'attach failed');
    } finally {
      setBusy(null);
    }
  }
  return (
    <aside className="right-sidebar tickets-right">
      <div className="tickets-right-head">
        <button
          className="icon-btn side-collapse-btn"
          title="Collapse panel"
          onClick={onCollapse}
        >
          <Icon.Chevron style={{ transform: 'rotate(-90deg)' }} />
        </button>
        <div className="tickets-right-id">{t.id}</div>
        <button className="icon-btn" title="Open full view" onClick={onExpand}><Icon.Expand /></button>
      </div>

      <div className="side-section">
        <div className="side-section-title">Properties</div>
        <div className="prop-list">
          <PropRow label="Status">
            <span className={'status-pill tone-' + meta.tone}>
              <span className="status-dot" />
              {meta.label}
            </span>
          </PropRow>
          <PropRow label="Source">
            <span className="chip-soft">{t.source}</span>
          </PropRow>
          <PropRow label="Task">
            <span className="chip-soft chip-task">{t.task}</span>
          </PropRow>
          <PropRow label="Team">
            <span className="chip-soft">{t.team}</span>
          </PropRow>
          <PropRow label={t.orderPlacedOn ? 'Order placed' : 'Order status'}>
            <span className="prop-text">{t.orderPlacedOn || t.orderStatus}</span>
          </PropRow>
        </div>
      </div>

      <div className="side-section">
        <div className="side-section-title">Assignment</div>
        <AssigneePicker ticket={t} onPatch={onPatch} />
      </div>

      {t.pendingChild ? (
        <div className="side-section">
          <div className="side-section-title">Pending child ticket</div>
          <div className="child-ticket">
            <div className="child-ticket-head">
              <Icon.User style={{ width: 14, height: 14, color: 'var(--muted)' }} />
              <span className="child-ticket-label">Assigned to</span>
              <button className="icon-btn" style={{ width: 24, height: 24, marginLeft: 'auto' }}><Icon.Pencil /></button>
            </div>
            <div className="child-ticket-email">{t.pendingChild.assigneeEmail}</div>
            <div className="child-ticket-meta">
              <span className="prop-label">Team</span>
              <span className="chip-soft">{t.pendingChild.team}</span>
            </div>
            <div className="child-ticket-objective">logistics_status</div>
            <div className="child-ticket-prompt">{t.pendingChild.prompt}</div>
            <button className="select-input"><span>Choose an option</span> <Icon.Chevron /></button>
          </div>
        </div>
      ) : (
        <div className="side-section">
          <button className="add-child-btn">
            <Icon.Plus /> Add child ticket
          </button>
        </div>
      )}

      <div className="side-section">
        <div className="side-tabs">
          <button className={'side-tab' + (resolTab === 'resolution' ? ' active' : '')} onClick={() => setResolTab('resolution')}>
            Resolution
          </button>
          <button className={'side-tab' + (resolTab === 'execution' ? ' active' : '')} onClick={() => setResolTab('execution')}>
            Execution
          </button>
        </div>
        <ResolutionForm ticket={t} onPatch={onPatch} />
      </div>

      <div className="side-section">
        <div className="side-section-title">Ticket actions</div>
        <div style={{ display: 'grid', gap: 6 }}>
          <button className="btn ghost" disabled={busy === 'merge'} onClick={() => setModal('merge')}>
            <Icon.Pencil /> Merge into another ticket
          </button>
          <button className="btn ghost" disabled={busy === 'split'} onClick={() => setModal('split')}>
            <Icon.Plus /> Split from message
          </button>
          <button className="btn ghost" disabled={busy === 'attach'} onClick={() => setModal('attach')}>
            <Icon.Inbox /> Attach a message
          </button>
          {flash && (
            <div
              style={{
                fontSize: 12,
                marginTop: 4,
                color: flash.ok ? 'var(--success, #22c55e)' : 'var(--danger, #ef4444)',
              }}
            >
              {flash.msg}
            </div>
          )}
        </div>
      </div>
      {modal === 'merge' && (
        <MergeTicketModal
          currentTicketId={t.id}
          onClose={() => setModal(null)}
          onSubmit={submitMerge}
          busy={busy === 'merge'}
        />
      )}
      {modal === 'split' && (
        <SplitTicketModal
          conversationId={t.conversationId}
          onClose={() => setModal(null)}
          onSubmit={submitSplit}
          busy={busy === 'split'}
        />
      )}
      {modal === 'attach' && (
        <AttachMessageModal
          conversationId={t.conversationId}
          onClose={() => setModal(null)}
          onSubmit={submitAttach}
          busy={busy === 'attach'}
        />
      )}
    </aside>
  );
}

// Voice-of-customer dispositions — the support team uses these to classify
// what the ticket was actually about at close time. Drives reporting.
const VOC_OPTIONS = [
  'product_inquiry',
  'bug_or_defect',
  'feature_request',
  'delivery_issue',
  'refund_requested',
  'billing_question',
  'account_help',
  'complaint',
  'praise',
  'other',
];

function AssigneePicker({ ticket, onPatch }) {
  // Admins (and anyone with the AgentOrAdmin role server-side) can reassign;
  // backend enforces the role — we just light up the picker for everyone
  // logged in. Members come from `/workspace/members`.
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    if (!open || members.length) return;
    api.get('/workspace/members')
      .then((rows) => setMembers(Array.isArray(rows) ? rows : []))
      .catch(() => setMembers([]));
  }, [open, members.length]);

  useEffect(() => {
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  async function assign(memberId, memberName) {
    setBusy(true); setErr(null);
    try {
      const updated = await api.patch(
        `/tickets/${encodeURIComponent(ticket.id)}`,
        { assigneeId: memberId },
      );
      onPatch?.(updated || { assignee: memberName });
      setOpen(false);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function unassign() {
    setBusy(true); setErr(null);
    try {
      const updated = await api.patch(
        `/tickets/${encodeURIComponent(ticket.id)}`,
        { assigneeId: null },
      );
      onPatch?.(updated || { assignee: null });
      setOpen(false);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally { setBusy(false); }
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {ticket.assignee ? (
        <div className="assign-row" onClick={() => setOpen((v) => !v)}
             style={{ cursor: 'pointer' }}>
          <div className="avatar sm" style={{ background: '#1e40af' }}>
            {ticket.assignee.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="assign-name">{ticket.assignee}</div>
            <div className="assign-sub">{ticket.team}</div>
          </div>
          <button className="icon-btn" title="Change assignee"><Icon.Pencil /></button>
        </div>
      ) : (
        <button className="assign-empty" onClick={() => setOpen((v) => !v)}>
          <Icon.User />
          <span>Unassigned</span>
          <span className="assign-empty-action">Assign</span>
        </button>
      )}
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, zIndex: 10,
          background: 'var(--card, #0f172a)', border: '1px solid var(--border)',
          borderRadius: 10, padding: 6, boxShadow: '0 18px 40px rgba(0,0,0,0.35)',
          maxHeight: 280, overflow: 'auto',
        }}>
          {ticket.assignee && (
            <button
              onClick={unassign}
              disabled={busy}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                width: '100%', padding: '8px 10px', background: 'transparent',
                border: 'none', color: 'var(--danger, #ef4444)', fontSize: 13,
                cursor: 'pointer', borderRadius: 6, textAlign: 'left',
              }}
            >
              <Icon.Close style={{ width: 14, height: 14 }} /> Unassign
            </button>
          )}
          {members.length === 0 ? (
            <div style={{ padding: 10, color: 'var(--muted)', fontSize: 12 }}>Loading…</div>
          ) : members.map((m) => {
            const selected = ticket.assignee === m.name;
            return (
              <button
                key={m.id}
                onClick={() => !selected && assign(m.id, m.name)}
                disabled={busy || selected}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  width: '100%', padding: '8px 10px',
                  background: selected ? 'var(--hover, rgba(255,255,255,0.05))' : 'transparent',
                  border: 'none', color: 'var(--text)', fontSize: 13,
                  cursor: selected ? 'default' : 'pointer', borderRadius: 6,
                  textAlign: 'left',
                }}
              >
                <div className="avatar sm" style={{ background: '#475569', flexShrink: 0 }}>
                  {m.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500 }}>{m.name}</div>
                  <div style={{ color: 'var(--muted)', fontSize: 11 }}>
                    {m.email} · {m.role}
                  </div>
                </div>
                {selected && <span style={{ color: 'var(--success, #22c55e)' }}>✓</span>}
              </button>
            );
          })}
          {err && (
            <div style={{ padding: 8, color: 'var(--danger, #ef4444)', fontSize: 12 }}>
              {err}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ResolutionForm({ ticket, onPatch }) {
  const [voc, setVoc] = useState(ticket.voc || '');
  const [notes, setNotes] = useState(ticket.resolutionNotes || '');
  const [state, setState] = useState('idle'); // 'idle' | 'saving' | 'saved' | 'error'
  const [err, setErr] = useState(null);
  const dirty = voc !== (ticket.voc || '') || notes !== (ticket.resolutionNotes || '');

  // Keep local state in sync if the ticket prop changes (new selection).
  useEffect(() => {
    setVoc(ticket.voc || '');
    setNotes(ticket.resolutionNotes || '');
    setState('idle');
  }, [ticket.id, ticket.voc, ticket.resolutionNotes]);

  async function save(e) {
    e?.preventDefault?.();
    if (!voc) { setErr('Select a VOC before saving.'); return; }
    setState('saving'); setErr(null);
    try {
      const updated = await api.patch(
        `/tickets/${encodeURIComponent(ticket.id)}`,
        { voc, resolutionNotes: notes },
      );
      onPatch?.(updated || { voc, resolutionNotes: notes });
      setState('saved');
      setTimeout(() => setState((s) => (s === 'saved' ? 'idle' : s)), 2000);
    } catch (e) {
      setErr(String(e?.message || e));
      setState('error');
    }
  }

  return (
    <form onSubmit={save}>
      <div className="form-group" style={{ marginBottom: 10 }}>
        <label>VOC <span style={{ color: 'var(--danger)' }}>*</span></label>
        <select
          value={voc}
          onChange={(e) => setVoc(e.target.value)}
          style={{ width: '100%', padding: '8px 10px', background: 'var(--card, #111827)',
                   color: 'var(--text)', border: '1px solid var(--border)',
                   borderRadius: 8, fontSize: 13 }}
        >
          <option value="">Select VOC</option>
          {VOC_OPTIONS.map((v) => (
            <option key={v} value={v}>{v.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>
      <div className="form-group" style={{ marginBottom: 10 }}>
        <label>Resolution notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Enter resolution details…"
          className="chat-input"
          style={{ minHeight: 90, width: '100%', resize: 'vertical' }}
        />
      </div>
      <div className="resolution-foot">
        <span className="resolution-status" style={{
          color: state === 'saved' ? 'var(--success, #22c55e)'
               : state === 'error' ? 'var(--danger, #ef4444)'
               : 'var(--muted)',
        }}>
          {state === 'saving' ? 'Saving…'
           : state === 'saved' ? '✓ Saved'
           : state === 'error' ? (err || 'Error')
           : dirty ? 'Unsaved changes'
           : (ticket.voc ? 'Disposition saved' : 'Disposition not set')}
        </span>
        <button type="submit" className="btn" disabled={state === 'saving' || !dirty}>
          {state === 'saving' ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  );
}

function ActionModalShell({ title, onClose, children, footer }) {
  return (
    <div className="action-modal-overlay" onClick={onClose}>
      <div className="action-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="action-modal-title">{title}</h3>
        <div className="action-modal-body">{children}</div>
        <div className="action-modal-footer">
          <button className="btn secondary" onClick={onClose}>Cancel</button>
          {footer}
        </div>
      </div>
    </div>
  );
}

function MergeTicketModal({ currentTicketId, onClose, onSubmit, busy }) {
  const [options, setOptions] = useState([]);
  const [pick, setPick] = useState('');
  useEffect(() => {
    let alive = true;
    api.get('/tickets?filter=all-open').then((res) => {
      if (!alive) return;
      const rows = (Array.isArray(res) ? res : res?.items || []).filter((x) => x.id !== currentTicketId);
      setOptions(rows);
      if (rows[0]) setPick(rows[0].id);
    }).catch(() => {});
    return () => { alive = false; };
  }, [currentTicketId]);
  return (
    <ActionModalShell
      title={`Merge ${currentTicketId} into…`}
      onClose={onClose}
      footer={
        <button
          className="btn"
          disabled={!pick || busy}
          onClick={() => onSubmit(pick)}
        >
          {busy ? 'Merging…' : 'Merge'}
        </button>
      }
    >
      <div style={{ display: 'grid', gap: 10 }}>
        <label>Target ticket
          <select value={pick} onChange={(e) => setPick(e.target.value)}>
            {options.length === 0 && <option value="">No other open tickets</option>}
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.id} — {o.subject || o.task} ({o.customer})
              </option>
            ))}
          </select>
        </label>
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>
          The source ticket becomes <b>closed</b>; its timeline is appended into the target.
        </div>
      </div>
    </ActionModalShell>
  );
}

function SplitTicketModal({ conversationId, onClose, onSubmit, busy }) {
  const [messages, setMessages] = useState([]);
  const [pick, setPick] = useState('');
  useEffect(() => {
    if (!conversationId) return;
    let alive = true;
    api.get(`/conversations/${encodeURIComponent(conversationId)}/messages`).then((res) => {
      if (!alive) return;
      const rows = Array.isArray(res) ? res : res?.items || [];
      setMessages(rows);
      if (rows[0]) setPick(rows[0].id);
    }).catch(() => {});
    return () => { alive = false; };
  }, [conversationId]);
  return (
    <ActionModalShell
      title="Split new ticket from a message"
      onClose={onClose}
      footer={
        <button
          className="btn"
          disabled={!pick || busy}
          onClick={() => onSubmit(pick)}
        >
          {busy ? 'Splitting…' : 'Split'}
        </button>
      }
    >
      <div style={{ display: 'grid', gap: 10 }}>
        {!conversationId && <div style={{ fontSize: 13, color: 'var(--danger)' }}>No conversation linked to this ticket.</div>}
        <label>Start new ticket from message
          <select value={pick} onChange={(e) => setPick(e.target.value)}>
            {messages.length === 0 && <option value="">No messages</option>}
            {messages.map((m) => (
              <option key={m.id} value={m.id}>
                [{m.at}] {m.from}: {(m.text || '').slice(0, 60)}
              </option>
            ))}
          </select>
        </label>
      </div>
    </ActionModalShell>
  );
}

function AttachMessageModal({ conversationId, onClose, onSubmit, busy }) {
  const [messages, setMessages] = useState([]);
  const [pick, setPick] = useState('');
  useEffect(() => {
    if (!conversationId) return;
    let alive = true;
    api.get(`/conversations/${encodeURIComponent(conversationId)}/messages`).then((res) => {
      if (!alive) return;
      const rows = Array.isArray(res) ? res : res?.items || [];
      setMessages(rows);
      if (rows[0]) setPick(rows[0].id);
    }).catch(() => {});
    return () => { alive = false; };
  }, [conversationId]);
  return (
    <ActionModalShell
      title="Attach a message to this ticket"
      onClose={onClose}
      footer={
        <button
          className="btn"
          disabled={!pick || busy}
          onClick={() => onSubmit(pick)}
        >
          {busy ? 'Attaching…' : 'Attach'}
        </button>
      }
    >
      <div style={{ display: 'grid', gap: 10 }}>
        {!conversationId && <div style={{ fontSize: 13, color: 'var(--danger)' }}>No conversation linked to this ticket.</div>}
        <label>Pick a message
          <select value={pick} onChange={(e) => setPick(e.target.value)}>
            {messages.length === 0 && <option value="">No messages</option>}
            {messages.map((m) => (
              <option key={m.id} value={m.id}>
                [{m.at}] {m.from}: {(m.text || '').slice(0, 60)}
              </option>
            ))}
          </select>
        </label>
      </div>
    </ActionModalShell>
  );
}

/* ------------------------------------------------------------------
   Expanded ticket detail modal
   ------------------------------------------------------------------ */

function formatCustomValue(v) {
  if (Array.isArray(v)) {
    return v.length === 0
      ? <span className="cf-empty">None</span>
      : <span>{v.join(', ')}</span>;
  }
  if (typeof v === 'boolean') {
    return (
      <span className={'cf-bool ' + (v ? 'yes' : 'no')}>
        {v ? 'Yes' : 'No'}
      </span>
    );
  }
  if (v === null || v === undefined || v === '') {
    return <span className="cf-empty">—</span>;
  }
  return <span>{String(v)}</span>;
}

function CollapsibleSection({ title, icon, defaultOpen = true, children, count }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={'collapsible' + (open ? ' open' : '')}>
      <button className="collapsible-head" onClick={() => setOpen((v) => !v)}>
        {icon ? <span className="collapsible-icon">{icon}</span> : null}
        <span className="collapsible-title">{title}</span>
        {typeof count === 'number' && <span className="collapsible-count">{count}</span>}
        <Icon.Chevron className={'collapsible-caret' + (open ? ' up' : '')} />
      </button>
      {open && <div className="collapsible-body anim-slide-down">{children}</div>}
    </div>
  );
}

function TicketDetailModal({ t, onClose }) {
  const meta = statusMeta[t.status] || { label: t.status || 'Queued', tone: 'info' };
  const order = t.order || null;
  const items = order?.items || [];
  const cf = t.customFields || {};
  const cd = t.customerDetails || {};
  const customerName = t.customer || 'Unknown';
  const navigate = useNavigate();
  const goToProfile = () => {
    const phone = t.customerDetails?.phone;
    const params = new URLSearchParams();
    if (phone) params.set('phone', phone);
    else if (t.customer) params.set('name', t.customer);
    onClose?.();
    navigate('/customers' + (params.toString() ? `?${params}` : ''));
  };
  return (
    <div className="modal-backdrop anim-fade-in" onClick={onClose}>
      <div
        className="modal-shell anim-slide-up"
        role="dialog"
        aria-modal="true"
        aria-label={`Ticket ${t.id}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div className="modal-head-left">
            <button className="icon-btn" onClick={onClose} title="Close (Esc)"><Icon.Close /></button>
            <div className="modal-title">
              <span className="modal-title-text">Ticket</span>
              <span className="modal-title-id">{t.id}</span>
              <span className={'status-pill tone-' + meta.tone}>
                <span className="status-dot" />
                {meta.label}
              </span>
            </div>
          </div>
          <div className="modal-head-right">
            <button className="btn ghost modal-head-btn" title="Copy link">
              <Icon.Paperclip /> <span>Copy link</span>
            </button>
            <button className="btn secondary modal-head-btn" onClick={onClose}>
              <Icon.Close /> <span>Close</span>
            </button>
          </div>
        </header>

        <div className="modal-body">
          {/* LEFT column */}
          <section className="modal-col modal-col-left">
            <div className="modal-panel">
              <div className="modal-panel-head">
                <span>Customer</span>
                <button className="link link-btn" onClick={goToProfile}>View profile <Icon.Chevron style={{ transform: 'rotate(-90deg)', width: 12, height: 12 }} /></button>
              </div>
              <div className="customer-top-row">
                <div className="avatar" style={{ background: '#1e40af' }}>
                  {customerName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="customer-top-name">{customerName}</div>
                  <div className="customer-top-sub">Customer since {cd.since || '—'}</div>
                </div>
              </div>
              <div className="kv-list">
                <div className="kv-row"><Icon.Phone className="icon" /><span>{cd.phone || '—'}</span></div>
                <div className="kv-row"><Icon.Mail className="icon" /><span>{cd.email || '—'}</span></div>
                <div className="kv-row" style={{ alignItems: 'flex-start' }}>
                  <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
                  <span>{cd.address || '—'}</span>
                </div>
              </div>
              <div className="customer-metric-row">
                <div className="customer-metric">
                  <div className="metric-value">{cd.orders ?? 0}</div>
                  <div className="metric-label">Orders</div>
                </div>
                <div className="customer-metric">
                  <div className="metric-value">{cd.returns ?? 0}</div>
                  <div className="metric-label">Returns</div>
                </div>
              </div>
            </div>

            {order && (
              <div className="modal-panel">
                <CollapsibleSection
                  title="Order"
                  icon={<Icon.ShoppingBag />}
                  count={items.length}
                >
                  <div className="order-summary-grid">
                    <div>
                      <div className="muted-tiny">Order ID</div>
                      <div className="order-summary-value">#{order.id}</div>
                    </div>
                    <div>
                      <div className="muted-tiny">Total</div>
                      <div className="order-summary-value">₹{Number(order.price || 0).toLocaleString()}</div>
                    </div>
                    <div>
                      <div className="muted-tiny">Placed on</div>
                      <div className="order-summary-value">{order.placedOn || '—'}</div>
                    </div>
                    <div>
                      <div className="muted-tiny">Status</div>
                      <div className="order-summary-value">{t.orderStatus || '—'}</div>
                    </div>
                  </div>

                  <div className="item-list">
                    {items.map((it, i) => (
                      <div key={i} className="item-list-row">
                        <div className="order-thumb"><Icon.ShoppingBag /></div>
                        <div className="item-list-meta">
                          <div className="item-list-name">{it.name}</div>
                          <div className="item-list-sub">SKU {it.sku} · Qty {it.qty}</div>
                        </div>
                        <div className="item-list-price">₹{Number((it.price || 0) * (it.qty || 1)).toLocaleString()}</div>
                      </div>
                    ))}
                  </div>
                </CollapsibleSection>
              </div>
            )}
          </section>

          {/* CENTER column */}
          <section className="modal-col modal-col-center">
            <div className="modal-panel">
              <div className="modal-panel-head"><span>Overview</span></div>
              <div className="overview-grid">
                <OverviewField label="Status">
                  <span className={'status-pill tone-' + meta.tone}>
                    <span className="status-dot" /> {meta.label}
                  </span>
                </OverviewField>
                <OverviewField label="Source">
                  <span className="chip-soft">{t.source}</span>
                </OverviewField>
                <OverviewField label="Task">
                  <span className="chip-soft chip-task">{t.task}</span>
                </OverviewField>
                <OverviewField label="Team">
                  <span className="chip-soft">{t.team}</span>
                </OverviewField>
              </div>
              <div className="overview-assignee">
                <div className="overview-field-label">Assignee</div>
                {t.assignee ? (
                  <div className="assign-row" style={{ margin: 0 }}>
                    <div className="avatar sm" style={{ background: '#1e40af' }}>
                      {t.assignee.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="assign-name">{t.assignee}</div>
                      <div className="assign-sub">{t.team}</div>
                    </div>
                    <button className="icon-btn" title="Change assignee"><Icon.Pencil /></button>
                  </div>
                ) : (
                  <div className="assign-empty" style={{ margin: 0 }}>
                    <Icon.User />
                    <span>Unassigned</span>
                    <span className="assign-empty-action">Assign</span>
                  </div>
                )}
              </div>
            </div>

            <div className="modal-panel">
              <div className="modal-panel-head">
                <span>Notes</span>
                <button className="link link-btn"><Icon.Plus /> Add note</button>
              </div>
              <div className="notes-modal-list">
                {(t.notes || []).length === 0 && (
                  <div className="notes-empty">No internal notes yet.</div>
                )}
                {(t.notes || []).map((n, i) => (
                  <div key={i} className="note-card">
                    <div className="note-meta">
                      <span className="note-author">{n.author}</span>
                      <span>{n.at}</span>
                    </div>
                    <div className="note-text">{n.text}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="modal-panel">
              <div className="modal-panel-head"><span>Resolution</span></div>
              <div className="form-group">
                <label>VOC <span style={{ color: 'var(--danger)' }}>*</span></label>
                <button className="select-input"><span>Select VOC</span> <Icon.Chevron /></button>
              </div>
              <div className="form-group">
                <label>Resolution notes</label>
                <textarea className="chat-input" placeholder="Enter resolution details…" style={{ minHeight: 100 }} />
              </div>
              <div className="resolution-foot">
                <span className="resolution-status">Disposition saved</span>
                <button className="btn">Save</button>
              </div>
            </div>
          </section>

          {/* RIGHT column */}
          <section className="modal-col modal-col-right">
            <div className="modal-panel">
              <div className="modal-panel-head"><span>Activity</span></div>
              <div className="timeline">
                {(t.timeline || []).map((e, i) => (
                  <div key={i} className="tl-item">
                    <div>
                      <div className="txt">{e.text}</div>
                      <div className="sub">{e.at}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {Object.keys(cf).length > 0 && (
              <div className="modal-panel">
                <div className="modal-panel-head"><span>Custom fields</span></div>
                <div className="prop-list">
                  {Object.entries(cf).map(([k, v]) => (
                    <div key={k} className="cf-row">
                      <div className="cf-label">{k}</div>
                      <div className="cf-value">{formatCustomValue(v)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function OverviewField({ label, children }) {
  return (
    <div className="overview-field">
      <div className="overview-field-label">{label}</div>
      <div className="overview-field-value">{children}</div>
    </div>
  );
}
