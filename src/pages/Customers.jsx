import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { useGlobalSearch } from '../hooks/useGlobalSearch.jsx';

const avatarColors = ['#1e40af', '#0ea5e9', '#16a34a', '#f97316', '#ec4899', '#64748b', '#dc2626', '#0891b2'];
const initials = (name) =>
  name.split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
const colorFor = (seed) => avatarColors[(seed || 'C').charCodeAt(0) % avatarColors.length];

const SORT_OPTIONS = [
  { key: 'recent', label: 'Recently active' },
  { key: 'name', label: 'Name A–Z' },
  { key: 'orders-desc', label: 'Orders: most first' },
  { key: 'tickets-desc', label: 'Tickets: most first' },
];

const FILTERS = [
  { key: 'all', label: 'All customers', match: () => true },
  { key: 'with-orders', label: 'With orders', match: (c) => (c.orders ?? 0) > 0 },
  { key: 'with-tickets', label: 'With open tickets', match: (c) => (c.tickets || []).some((t) => t.status === 'In Progress' || t.status === 'Blocked') },
  { key: 'with-returns', label: 'With returns', match: (c) => (c.returns ?? 0) > 0 },
];

const customers = [
  {
    id: 'c-aftab',
    name: 'aftab sayyed',
    phone: '918898400709',
    phoneDisplay: '+91 88 9840 0709',
    email: 'lionafsay@gmail.com',
    since: 'Mar 2026',
    lastActive: 'Apr 22, 2026',
    channel: 'WhatsApp',
    orders: 2,
    returns: 0,
    address: 'Floor-Ground Floor, test, test, THANE, Maharashtra, 400608, India',
    otherAddresses: [
      'Floor-1, 102 JEEVAN SAPNA APARTMENT JUHU, VERSOVA LINK ROAD ANDHERI WEST NEAR BANANA LEAF RESTAURENT, MUMBAI, Maharashtra, 400053, India',
    ],
    recentOrders: [
      {
        id: '20640127',
        time: '6:00 PM, Mar 9, 26',
        status: 'Cancellation Rejected on 11 April 2026',
        statusTone: 'success',
        total: 11500,
        itemsCount: 2,
        items: [
          { name: 'Test Chair', qty: 1, price: 8000 },
          { name: 'Test Custom Mattress', qty: 1, price: 3500 },
        ],
      },
      {
        id: '20635479',
        time: '4:33 PM, Mar 4, 26',
        status: 'Delivered on 4 March 2026',
        statusTone: 'success',
        total: 24999,
        itemsCount: 1,
        items: [{ name: 'SmartGRID Mattress', qty: 1, price: 24999 }],
      },
    ],
    tickets: [
      { id: '#3777', status: 'In Progress', statusTone: 'info', source: 'WhatsApp', task: 'product_issue_report', team: 'CRM L1', updated: 'Apr 22' },
      { id: '#3759', status: 'Blocked', statusTone: 'danger', source: 'WhatsApp', task: 'delay_delivery', team: 'CRM L1', updated: 'Apr 10' },
    ],
  },
  {
    id: 'c-rajendra',
    name: 'rajendra .',
    phone: '919870231100',
    phoneDisplay: '+91 98 7023 1100',
    email: 'rajendra@example.com',
    since: 'Apr 2026',
    lastActive: 'Apr 14, 2026',
    channel: 'WhatsApp',
    orders: 1,
    returns: 0,
    address: 'Floor-Ground Floor, ANDHERI J b nagar metro station, MUMBAI, Maharashtra, 400059, India',
    otherAddresses: [],
    recentOrders: [
      {
        id: '20466915',
        time: 'Aug 4, 2025',
        status: 'Picked up by Customer',
        statusTone: 'info',
        total: 1899,
        itemsCount: 1,
        items: [{ name: 'Wedge Cushion', qty: 1, price: 1899 }],
      },
    ],
    tickets: [
      { id: '#3770', status: 'In Progress', statusTone: 'info', source: 'WhatsApp', task: 'not_delivered_agent_handoff', team: 'CRM L1', updated: 'Apr 21' },
    ],
  },
  {
    id: 'c-miloni',
    name: 'Miloni Boricha',
    phone: '919167534112',
    phoneDisplay: '+91 91 6753 4112',
    email: 'miloni.b@example.com',
    since: 'Jan 2026',
    lastActive: 'Apr 22, 2026',
    channel: 'WhatsApp',
    orders: 0,
    returns: 0,
    address: null,
    otherAddresses: [],
    recentOrders: [],
    tickets: [],
  },
  {
    id: 'c-dixit',
    name: 'Dixit Shah',
    phone: '919123123123',
    phoneDisplay: '+91 91 2312 3123',
    email: 'dixit@example.com',
    since: 'Feb 2026',
    lastActive: 'Apr 14, 2026',
    channel: 'Call',
    orders: 3,
    returns: 1,
    address: 'HSR Layout, Bangalore, Karnataka, 560102, India',
    otherAddresses: [],
    recentOrders: [
      {
        id: '20466820',
        time: 'Apr 10, 2026',
        status: 'Refund initiated',
        statusTone: 'warning',
        total: 45999,
        itemsCount: 1,
        items: [{ name: 'SmartGRID Mattress', qty: 1, price: 45999 }],
      },
      {
        id: '20466719',
        time: 'Apr 12, 2026',
        status: 'In transit',
        statusTone: 'info',
        total: 9999,
        itemsCount: 2,
        items: [{ name: 'Ortho Pillow', qty: 2, price: 4999.5 }],
      },
    ],
    tickets: [
      { id: '#3765', status: 'Queued', statusTone: 'warning', source: 'Call', task: 'refund_pending', team: 'CRM L2', updated: 'Apr 14' },
      { id: '#3764', status: 'Queued', statusTone: 'warning', source: 'Call', task: 'delivery_followup', team: 'CRM L1', updated: 'Apr 14' },
    ],
  },
  {
    id: 'c-ashwini',
    name: 'Ashwini Sawant',
    phone: '919820345612',
    phoneDisplay: '+91 98 2034 5612',
    email: 'ashwini2186@gmail.com',
    since: 'Dec 2025',
    lastActive: 'Apr 12, 2026',
    channel: 'WhatsApp',
    orders: 4,
    returns: 2,
    address: 'Flat 203, Sunrise Heights, Pune, Maharashtra, 411001, India',
    otherAddresses: [],
    recentOrders: [
      {
        id: '20401234',
        time: 'Feb 18, 2026',
        status: 'Delivered on 22 February 2026',
        statusTone: 'success',
        total: 6499,
        itemsCount: 1,
        items: [{ name: 'Cloud Pillow', qty: 1, price: 6499 }],
      },
    ],
    tickets: [],
  },
  {
    id: 'c-shaurya',
    name: 'SHAURYA SINGH',
    phone: '917678123456',
    phoneDisplay: '+91 76 7812 3456',
    email: 'shaurya@example.com',
    since: 'Apr 2026',
    lastActive: 'Apr 10, 2026',
    channel: 'WhatsApp',
    orders: 1,
    returns: 0,
    address: 'Sector 21, Noida, Uttar Pradesh, 201301, India',
    otherAddresses: [],
    recentOrders: [
      {
        id: '20598712',
        time: 'Apr 8, 2026',
        status: 'Out for delivery',
        statusTone: 'info',
        total: 18999,
        itemsCount: 1,
        items: [{ name: 'SensAI Sleep Analyzer Pillow', qty: 1, price: 18999 }],
      },
    ],
    tickets: [
      { id: '#3754', status: 'In Progress', statusTone: 'info', source: 'WhatsApp', task: 'delivery_followup', team: 'CRM L1', updated: 'Apr 10' },
    ],
  },
  {
    id: 'c-hamzah',
    name: 'Hamzah',
    phone: '919812345678',
    phoneDisplay: '+91 98 1234 5678',
    email: 'hamzah@example.com',
    since: 'Nov 2025',
    lastActive: 'Apr 20, 2026',
    channel: 'WhatsApp',
    orders: 2,
    returns: 0,
    address: 'Juhu, Mumbai, Maharashtra, 400049, India',
    otherAddresses: [],
    recentOrders: [
      {
        id: '20512345',
        time: 'Mar 15, 2026',
        status: 'Delivered on 18 March 2026',
        statusTone: 'success',
        total: 2499,
        itemsCount: 1,
        items: [{ name: 'Travel Neck Pillow', qty: 1, price: 2499 }],
      },
    ],
    tickets: [],
  },
  {
    id: 'c-fiona',
    name: 'Fiona puthur',
    phone: '919090807060',
    phoneDisplay: '+91 90 9080 7060',
    email: 'fiona.p@example.com',
    since: 'Oct 2025',
    lastActive: 'Apr 15, 2026',
    channel: 'WhatsApp',
    orders: 0,
    returns: 0,
    address: null,
    otherAddresses: [],
    recentOrders: [],
    tickets: [],
  },
];

export default function Customers() {
  const [params, setParams] = useSearchParams();
  const findByParams = () => {
    const id = params.get('id');
    if (id) {
      const hit = customers.find((c) => c.id === id);
      if (hit) return hit.id;
    }
    const phone = params.get('phone');
    if (phone) {
      const hit = customers.find((c) => c.phone === phone);
      if (hit) return hit.id;
    }
    const name = params.get('name');
    if (name) {
      const lower = name.toLowerCase();
      const hit = customers.find((c) => c.name.toLowerCase() === lower);
      if (hit) return hit.id;
    }
    return null;
  };

  const [selectedId, setSelectedId] = useState(() => findByParams() || customers[0].id);
  const { query: search } = useGlobalSearch();
  const [sort, setSort] = useState('recent');
  const [sortOpen, setSortOpen] = useState(false);
  const [filterKey, setFilterKey] = useState('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef(null);
  const sortRef = useRef(null);

  useEffect(() => {
    const matchId = findByParams();
    if (matchId) {
      setSelectedId(matchId);
      const next = new URLSearchParams(params);
      next.delete('id');
      next.delete('phone');
      next.delete('name');
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const selected = customers.find((c) => c.id === selectedId);

  useEffect(() => {
    function onClick(e) {
      if (filterRef.current && !filterRef.current.contains(e.target)) setFilterOpen(false);
      if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const filterCounts = useMemo(() => {
    const out = {};
    for (const f of FILTERS) out[f.key] = customers.filter(f.match).length;
    return out;
  }, []);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filterFn = FILTERS.find((f) => f.key === filterKey)?.match || (() => true);
    let out = customers.filter(filterFn).filter((c) => {
      if (!q) return true;
      return (
        c.name.toLowerCase().includes(q) ||
        (c.phone || '').toLowerCase().includes(q) ||
        (c.email || '').toLowerCase().includes(q)
      );
    });
    switch (sort) {
      case 'name':
        out = [...out].sort((a, b) => a.name.localeCompare(b.name));
        break;
      case 'orders-desc':
        out = [...out].sort((a, b) => (b.orders ?? 0) - (a.orders ?? 0));
        break;
      case 'tickets-desc':
        out = [...out].sort((a, b) => (b.tickets?.length ?? 0) - (a.tickets?.length ?? 0));
        break;
      default:
        break;
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
            <div className="top-bar-title">Customers</div>
            <span className="top-bar-subcount">{customers.length} total</span>
          </>
        }
      />
      <div className="main">
        <div className="pane">
          {/* Customer list */}
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
                    <>{visible.length} {visible.length === 1 ? 'customer' : 'customers'}</>
                  )}
                </div>
                <div className="dropdown" ref={sortRef} style={{ display: 'flex' }}>
                  <button
                    className={'search-icon-btn boxed' + (sortOpen || sort !== 'recent' ? ' active' : '')}
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
              {visible.map((c) => (
                <CustomerRow
                  key={c.id}
                  c={c}
                  selected={c.id === selectedId}
                  onClick={() => setSelectedId(c.id)}
                />
              ))}
              {visible.length === 0 && (
                <div className="tickets-empty">
                  <Icon.InboxEmpty />
                  <div>No customers match your search.</div>
                </div>
              )}
            </div>
          </div>

          {/* Customer details */}
          {selected ? (
            <CustomerDetail c={selected} />
          ) : (
            <div className="center-pane" style={{ alignItems: 'center', justifyContent: 'center' }}>
              <div className="empty-state">
                <Icon.InboxEmpty />
                <div className="empty-state-text">Select a customer to view details.</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function CustomerRow({ c, selected, onClick }) {
  return (
    <div className={'ticket-card customer-card' + (selected ? ' selected' : '')} onClick={onClick}>
      <div className="ticket-card-top">
        <div className="avatar" style={{ background: colorFor(c.name) }}>
          {initials(c.name)}
        </div>
        <div className="ticket-card-headline">
          <span className="ticket-customer" style={{ fontWeight: 700 }}>{c.name}</span>
        </div>
        <span className="ticket-date">{c.lastActive?.split(',')[0]}</span>
      </div>

      <div className="customer-row-meta">
        <span className="customer-row-contact">
          <Icon.Phone style={{ width: 12, height: 12 }} />
          {c.phoneDisplay || c.phone}
        </span>
        {c.email && (
          <span className="customer-row-contact">
            <Icon.Mail style={{ width: 12, height: 12 }} />
            <span className="customer-row-email">{c.email}</span>
          </span>
        )}
      </div>

      <div className="ticket-card-foot">
        <span className="chip-soft customer-chip">
          <Icon.ShoppingBag /> {c.orders ?? 0} {c.orders === 1 ? 'order' : 'orders'}
        </span>
        {(c.returns ?? 0) > 0 && (
          <span className="chip-soft customer-chip">
            <Icon.Refresh /> {c.returns} {c.returns === 1 ? 'return' : 'returns'}
          </span>
        )}
        {(c.tickets?.length ?? 0) > 0 && (
          <span className="chip-soft customer-chip tone-info">
            <Icon.Ticket style={{ width: 12, height: 12 }} /> {c.tickets.length} {c.tickets.length === 1 ? 'ticket' : 'tickets'}
          </span>
        )}
      </div>
    </div>
  );
}

function CustomerDetail({ c }) {
  const [ordersOpen, setOrdersOpen] = useState(true);
  const [ticketsOpen, setTicketsOpen] = useState(true);

  return (
    <div className="center-pane customers-detail">
      <div className="detail-header">
        <div className="detail-header-left" style={{ gap: 14 }}>
          <div className="avatar lg" style={{ background: colorFor(c.name) }}>
            {initials(c.name)}
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="detail-id" style={{ fontSize: 18 }}>{c.name}</div>
            <div className="customer-top-sub">Customer since {c.since} · Last active {c.lastActive}</div>
          </div>
        </div>
        <div className="detail-header-right">
          <button className="icon-btn" title="Refresh"><Icon.Refresh /></button>
          <button className="icon-btn" title="More"><Icon.More /></button>
        </div>
      </div>

      <div className="detail-body customers-detail-body">
        {/* Customer Profile */}
        <div className="panel">
          <div className="panel-head">
            <span><Icon.User style={{ width: 14, height: 14, marginRight: 6, verticalAlign: 'middle' }} />Customer Profile</span>
          </div>
          <div className="kv-list">
            <div className="kv-row"><Icon.Phone className="icon" /><span>{c.phoneDisplay || c.phone}</span></div>
            {c.email && <div className="kv-row"><Icon.Mail className="icon" /><span>{c.email}</span></div>}
            {c.address && (
              <div className="kv-row" style={{ alignItems: 'flex-start' }}>
                <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
                <span>{c.address}</span>
              </div>
            )}
          </div>
          <div className="customer-stats" style={{ marginTop: 14, marginBottom: 0 }}>
            <div className="customer-stat stat-orders">
              <div className="customer-stat-label">ORDERS</div>
              <div className="customer-stat-row">
                <div className="customer-stat-value">{c.orders ?? 0}</div>
                <Icon.ShoppingBag style={{ opacity: 0.55 }} />
              </div>
            </div>
            <div className="customer-stat stat-returns">
              <div className="customer-stat-label">RETURNS</div>
              <div className="customer-stat-row">
                <div className="customer-stat-value">{c.returns ?? 0}</div>
                <Icon.Refresh style={{ opacity: 0.55, width: 18, height: 18 }} />
              </div>
            </div>
          </div>
          {c.otherAddresses && c.otherAddresses.length > 0 && (
            <div className="customer-section">
              <div className="customer-section-head">
                <Icon.MapPin />
                <span>Other Addresses ({c.otherAddresses.length})</span>
              </div>
              <div className="customer-other-addresses">
                {c.otherAddresses.map((addr, i) => (
                  <div key={i} className="customer-address alt">{addr}</div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Order Details */}
        <div className="panel">
          <div className="panel-head">
            <button
              className="panel-head-toggle"
              onClick={() => setOrdersOpen((v) => !v)}
            >
              <Icon.Chevron style={{ transform: ordersOpen ? 'none' : 'rotate(-90deg)', width: 12, height: 12 }} />
              <Icon.ShoppingBag style={{ width: 14, height: 14, marginRight: 4 }} />
              <span>Order Details</span>
              <span className="order-count">{c.recentOrders?.length || 0}</span>
            </button>
          </div>
          {ordersOpen && (
            c.recentOrders && c.recentOrders.length > 0 ? (
              <div className="recent-orders">
                {c.recentOrders.map((o) => (
                  <OrderCard key={o.id} order={o} />
                ))}
              </div>
            ) : (
              <div className="empty-inline">
                <Icon.InboxEmpty />
                <div>No orders yet</div>
              </div>
            )
          )}
        </div>

        {/* Support Tickets */}
        <div className="panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-head">
            <button
              className="panel-head-toggle"
              onClick={() => setTicketsOpen((v) => !v)}
            >
              <Icon.Chevron style={{ transform: ticketsOpen ? 'none' : 'rotate(-90deg)', width: 12, height: 12 }} />
              <Icon.Document style={{ width: 14, height: 14, marginRight: 4 }} />
              <span>Support Tickets</span>
              <span className="order-count">{c.tickets?.length || 0}</span>
            </button>
          </div>
          {ticketsOpen && (
            c.tickets && c.tickets.length > 0 ? (
              <div className="customer-tickets-list">
                {c.tickets.map((t) => (
                  <TicketRow key={t.id} t={t} />
                ))}
              </div>
            ) : (
              <div className="empty-inline">
                <Icon.InboxEmpty />
                <div>No support tickets</div>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}

function OrderCard({ order }) {
  return (
    <div className="recent-order">
      <div className="recent-order-top">
        <div className="recent-order-id">#{order.id}</div>
        <div className="recent-order-time">{order.time}</div>
      </div>
      {order.status && (
        <div className={'recent-order-status tone-' + (order.statusTone || 'info')}>
          {order.status}
        </div>
      )}
      <div className="recent-order-meta">
        <span className="recent-order-total"><Icon.Info style={{ width: 12, height: 12 }} /> ₹{order.total.toLocaleString()}</span>
        <span className="recent-order-items">
          <Icon.ShoppingBag /> {order.itemsCount} {order.itemsCount === 1 ? 'item' : 'items'}
        </span>
      </div>
      {order.items && order.items.length > 0 && (
        <div className="recent-order-items-list">
          {order.items.map((it, i) => (
            <div key={i} className="recent-order-item">
              <span className="recent-order-item-name">{it.name}</span>
              <span className="recent-order-item-qty">× {it.qty}</span>
              <span className="recent-order-item-price">₹{it.price.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TicketRow({ t }) {
  const navigate = useNavigate();
  const openTicket = () => {
    navigate(`/tickets?id=${encodeURIComponent(t.id)}`);
  };
  return (
    <div
      className="customer-ticket-row"
      onClick={openTicket}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openTicket(); } }}
      role="button"
      tabIndex={0}
      style={{ cursor: 'pointer' }}
    >
      <div className="customer-ticket-top">
        <span className="ticket-id">{t.id}</span>
        <span className={'status-pill tone-' + (t.statusTone || 'info')}>
          <span className="status-dot" />
          {t.status}
        </span>
        <span className="customer-ticket-updated">{t.updated}</span>
      </div>
      <div className="customer-ticket-meta">
        <span className="chip-soft">{t.source}</span>
        <span className="chip-soft chip-task">{t.task}</span>
        <span className="chip-soft">{t.team}</span>
      </div>
    </div>
  );
}
