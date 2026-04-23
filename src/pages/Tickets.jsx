import { useEffect, useMemo, useRef, useState } from 'react';
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
  { key: 'my-tickets', label: 'My Tickets', match: (t) => t.assignee === 'Aniket Singh' },
  { key: 'unassigned', label: 'Unassigned', match: (t) => !t.assignee },
  { key: 'blocked', label: 'Blocked', match: (t) => t.status === 'blocked' },
  { key: 'my-created-progress', label: 'My Created · In Progress', match: (t) => t.status === 'assigned' },
  { key: 'my-created', label: 'My Created', match: () => true },
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
        setTickets(items);
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
        setTickets((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...detail } : t));
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
  const workspaceId = loadAuth()?.workspaceId;
  useRealtimeTopic(workspaceId ? `inbox:${workspaceId}` : null, (evt) => {
    if (evt.type === 'ticket.updated') {
      setTickets((prev) => prev.map((t) => t.id === evt.data.id ? { ...t, ...evt.data } : t));
    } else if (evt.type === 'ticket.created') {
      setTickets((prev) => prev.some((t) => t.id === evt.data.id) ? prev : [evt.data, ...prev]);
    }
  });
  useRealtimeTopic(
    selectedId ? `ticket:${selectedId.replace(/^#/, '')}` : null,
    (evt) => {
      if (evt.type === 'ticket.updated') {
        setTickets((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...evt.data } : t));
      } else if (evt.type === 'ticket.timeline.appended') {
        // refetch the detail to pick up the appended timeline entry
        api.get(`/tickets/${encodeURIComponent(selectedId)}`).then((detail) => {
          if (!detail) return;
          setTickets((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...detail } : t));
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
      return (
        t.id.toLowerCase().includes(q) ||
        t.customer.toLowerCase().includes(q) ||
        t.task.toLowerCase().includes(q) ||
        (t.assignee || '').toLowerCase().includes(q)
      );
    });
    const idNum = (t) => parseInt(t.id.replace(/\D/g, ''), 10) || 0;
    switch (sort) {
      case 'oldest':    out = [...out].sort((a, b) => idNum(a) - idNum(b)); break;
      case 'sla-asc':   out = [...out].sort((a, b) => slaHours(a.sla) - slaHours(b.sla)); break;
      case 'sla-desc':  out = [...out].sort((a, b) => slaHours(b.sla) - slaHours(a.sla)); break;
      case 'customer':  out = [...out].sort((a, b) => a.customer.localeCompare(b.customer)); break;
      default:          out = [...out].sort((a, b) => idNum(b) - idNum(a));
    }
    return out;
  }, [search, sort, filterKey]);

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
            {tab === 'convo' && <EmptyBody label="Conversation thread" />}
            {tab === 'notes' && <EmptyBody label="Internal notes" />}
            {tab === 'customer' && <EmptyBody label="Customer profile" />}
          </div>

          {/* Right sidebar */}
          {selected && !sidebarCollapsed && (
            <TicketSidebar
              t={selected}
              onExpand={() => setExpandedOpen(true)}
              onCollapse={() => setSidebarCollapsed(true)}
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
  return (
    <div className="detail-body">
      {/* Customer */}
      <div className="panel">
        <div className="panel-head">
          <span>Customer</span>
          <button className="link link-btn">View details <Icon.Chevron style={{ transform: 'rotate(-90deg)', width: 12, height: 12 }} /></button>
        </div>
        <div className="customer-top-row">
          <div className="avatar" style={{ background: '#1e40af' }}>
            {t.customer.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="customer-top-name">{t.customer}</div>
            <div className="customer-top-sub">Customer since {t.customerDetails.since}</div>
          </div>
        </div>
        <div className="kv-list">
          <div className="kv-row"><Icon.Phone className="icon" /><span>{t.customerDetails.phone}</span></div>
          <div className="kv-row"><Icon.Mail className="icon" /><span>{t.customerDetails.email}</span></div>
          <div className="kv-row" style={{ alignItems: 'flex-start' }}>
            <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
            <span>{t.customerDetails.address}</span>
          </div>
        </div>
        <div className="customer-metric-row">
          <div className="customer-metric">
            <div className="metric-value">{t.customerDetails.orders}</div>
            <div className="metric-label">Orders</div>
          </div>
          <div className="customer-metric">
            <div className="metric-value">{t.customerDetails.returns}</div>
            <div className="metric-label">Returns</div>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="panel">
        <div className="panel-head"><span>Activity</span></div>
        <div className="timeline">
          {t.timeline.map((e, i) => (
            <div key={i} className="tl-item">
              <div>
                <div className="txt">{e.text}</div>
                <div className="sub">{e.at}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Order spans full width */}
      <div className="panel" style={{ gridColumn: '1 / -1' }}>
        <div className="panel-head">
          <span>Order</span>
          <span className="muted-small">Ordered {t.order.placedOn || 'Apr 22, 2026'}</span>
        </div>
        <div className="order-card">
          <div className="order-card-top">
            <div>
              <div className="order-id">#{t.order.id}</div>
              <div className="order-sub">
                <span className="muted-dot success" /> Paid
                <span className="order-sub-sep">·</span>
                {t.orderStatus || 'Processing'}
              </div>
            </div>
            <div className="order-total">₹{t.order.price.toLocaleString()}</div>
          </div>
          {(t.order.items || []).map((it, i) => (
            <div key={i} className="order-item">
              <div className="order-thumb"><Icon.ShoppingBag /></div>
              <div style={{ minWidth: 0 }}>
                <div className="order-item-name">{it.name}</div>
                <div className="order-item-qty">SKU: {it.sku} · Qty: {it.qty}</div>
              </div>
              <div className="order-item-price">₹{(it.price * it.qty).toLocaleString()}</div>
            </div>
          ))}
        </div>
      </div>
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

function PropRow({ label, children }) {
  return (
    <div className="prop-row">
      <div className="prop-label">{label}</div>
      <div className="prop-value">{children}</div>
    </div>
  );
}

function TicketSidebar({ t, onExpand, onCollapse }) {
  const [resolTab, setResolTab] = useState('resolution');
  const meta = statusMeta[t.status];
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
        {t.assignee ? (
          <div className="assign-row">
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
          <button className="assign-empty">
            <Icon.User />
            <span>Unassigned</span>
            <span className="assign-empty-action">Assign</span>
          </button>
        )}
        <div className="owner-row">
          <Icon.User style={{ color: 'var(--muted)' }} />
          <span className="owner-text">No owner</span>
          <button className="btn ghost owner-btn">
            <Icon.Plus /> Set owner
          </button>
        </div>
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
        <div className="form-group" style={{ marginBottom: 12 }}>
          <label>VOC <span style={{ color: 'var(--danger)' }}>*</span></label>
          <button className="select-input"><span>Select VOC</span> <Icon.Chevron /></button>
        </div>
        <div className="resolution-foot">
          <span className="resolution-status">Disposition saved</span>
          <button className="btn">Save</button>
        </div>
      </div>
    </aside>
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
  const meta = statusMeta[t.status];
  const items = t.order.items || [];
  const cf = t.customFields || {};
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
                  {t.customer.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="customer-top-name">{t.customer}</div>
                  <div className="customer-top-sub">Customer since {t.customerDetails.since}</div>
                </div>
              </div>
              <div className="kv-list">
                <div className="kv-row"><Icon.Phone className="icon" /><span>{t.customerDetails.phone}</span></div>
                <div className="kv-row"><Icon.Mail className="icon" /><span>{t.customerDetails.email}</span></div>
                <div className="kv-row" style={{ alignItems: 'flex-start' }}>
                  <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
                  <span>{t.customerDetails.address}</span>
                </div>
              </div>
              <div className="customer-metric-row">
                <div className="customer-metric">
                  <div className="metric-value">{t.customerDetails.orders}</div>
                  <div className="metric-label">Orders</div>
                </div>
                <div className="customer-metric">
                  <div className="metric-value">{t.customerDetails.returns}</div>
                  <div className="metric-label">Returns</div>
                </div>
              </div>
            </div>

            <div className="modal-panel">
              <CollapsibleSection
                title="Order"
                icon={<Icon.ShoppingBag />}
                count={items.length}
              >
                <div className="order-summary-grid">
                  <div>
                    <div className="muted-tiny">Order ID</div>
                    <div className="order-summary-value">#{t.order.id}</div>
                  </div>
                  <div>
                    <div className="muted-tiny">Total</div>
                    <div className="order-summary-value">₹{t.order.price.toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="muted-tiny">Placed on</div>
                    <div className="order-summary-value">{t.order.placedOn || '—'}</div>
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
                      <div className="item-list-price">₹{(it.price * it.qty).toLocaleString()}</div>
                    </div>
                  ))}
                </div>
              </CollapsibleSection>
            </div>
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
                {t.timeline.map((e, i) => (
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
