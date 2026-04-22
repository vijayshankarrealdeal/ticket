import { useState } from 'react';
import { Icon } from '../../components/Icons.jsx';

const initialStores = [
  {
    id: 'store-1',
    name: 'thesleepcompany.in',
    platform: 'Shopify',
    accent: '#95bf47',
    letter: 'S',
    status: 'connected',
    plan: 'Shopify Plus',
    lastSync: '2 minutes ago',
    orders30d: 1284,
    health: 98,
  },
  {
    id: 'store-2',
    name: 'thesleepcompany.ae',
    platform: 'Shopify',
    accent: '#95bf47',
    letter: 'S',
    status: 'syncing',
    plan: 'Shopify',
    lastSync: 'Syncing…',
    orders30d: 142,
    health: 92,
  },
  {
    id: 'store-3',
    name: 'wholesale portal',
    platform: 'Unicommerce',
    accent: '#9ab04a',
    letter: 'U',
    status: 'attention',
    plan: 'Enterprise',
    lastSync: '42 minutes ago · webhook delayed',
    orders30d: 38,
    health: 74,
  },
];

const recentEvents = [
  { id: 1, type: 'order', icon: Icon.ShoppingBag, tone: 'primary', title: 'Order #SC-18472', sub: 'Shopify · Mumbai', time: '2m ago' },
  { id: 2, type: 'sync', icon: Icon.Refresh, tone: 'info', title: 'Inventory synced', sub: '1,204 SKUs updated from Unicommerce', time: '14m ago' },
  { id: 3, type: 'issue', icon: Icon.AlertTriangle, tone: 'warning', title: 'Webhook retried', sub: 'orders/updated · Shopify AE · success on 2nd try', time: '1h ago' },
  { id: 4, type: 'order', icon: Icon.ShoppingBag, tone: 'primary', title: 'Order #SC-18469', sub: 'Shopify · Bengaluru', time: '1h ago' },
  { id: 5, type: 'refund', icon: Icon.Refresh, tone: 'danger', title: 'Refund processed', sub: '#SC-18202 · returned to original method', time: '3h ago' },
];

export default function ECommerce() {
  const [stores, setStores] = useState(initialStores);

  const disconnect = (id) => setStores((prev) => prev.filter((s) => s.id !== id));

  return (
    <div className="ch-page">
      <div className="ch-page-head">
        <div>
          <div className="ch-breadcrumb">
            <span>Channels</span>
            <Icon.Chevron className="ch-breadcrumb-sep" />
            <span className="ch-breadcrumb-current">E-Commerce</span>
          </div>
          <h1 className="ch-page-title">E-Commerce stores</h1>
          <p className="ch-page-sub">
            All storefronts currently piping orders, customers and inventory into your workspace.
          </p>
        </div>
        <div className="ch-page-head-actions">
          <button className="btn secondary"><Icon.Refresh /> Sync all</button>
          <button className="btn"><Icon.Plus /> Add store</button>
        </div>
      </div>

      <div className="ch-kpi-row">
        <Kpi label="Total orders · 30d" value="1,464" delta="+12%" tone="success" />
        <Kpi label="Active stores" value={stores.length} delta="+1 this month" tone="success" />
        <Kpi label="Avg sync latency" value="210ms" delta="-35ms" tone="success" />
        <Kpi label="Failed webhooks" value="3" delta="+1" tone="warning" />
      </div>

      <div className="ch-two-col">
        <section className="ch-section">
          <div className="ch-section-head">
            <div className="ch-section-title">
              <span className="ch-section-icon"><Icon.Store /></span>
              Connected stores
              <span className="ch-section-count">{stores.length}</span>
            </div>
            <div className="ch-section-head-right">
              <button className="ch-chip-btn"><Icon.Filter /> Filter</button>
              <button className="ch-chip-btn"><Icon.Sort /> Sort</button>
            </div>
          </div>

          <div className="ch-store-list">
            {stores.map((s) => (
              <StoreRow key={s.id} store={s} onDisconnect={() => disconnect(s.id)} />
            ))}
          </div>
        </section>

        <aside className="ch-activity">
          <div className="ch-activity-head">
            <div className="ch-section-title">
              <span className="ch-section-icon"><Icon.Activity /></span>
              Recent activity
            </div>
            <button className="ch-link">View all</button>
          </div>
          <div className="ch-activity-list">
            {recentEvents.map((e) => {
              const I = e.icon;
              return (
                <div key={e.id} className="ch-activity-item">
                  <div className={'ch-activity-icon tone-' + e.tone}>
                    <I />
                  </div>
                  <div className="ch-activity-body">
                    <div className="ch-activity-title">{e.title}</div>
                    <div className="ch-activity-sub">{e.sub}</div>
                  </div>
                  <div className="ch-activity-time">{e.time}</div>
                </div>
              );
            })}
          </div>
        </aside>
      </div>
    </div>
  );
}

function Kpi({ label, value, delta, tone = 'success' }) {
  return (
    <div className="ch-kpi">
      <div className="ch-kpi-label">{label}</div>
      <div className="ch-kpi-value">{value}</div>
      <div className={'ch-kpi-delta tone-' + tone}>{delta}</div>
    </div>
  );
}

function StoreRow({ store, onDisconnect }) {
  const statusMap = {
    connected: { label: 'Connected', tone: 'success' },
    syncing: { label: 'Syncing', tone: 'info' },
    attention: { label: 'Needs attention', tone: 'warning' },
  };
  const status = statusMap[store.status];
  return (
    <div className="ch-store-row">
      <div className="ch-store-left">
        <div className="ch-card-logo" style={{ background: store.accent }}>
          {store.letter}
        </div>
        <div>
          <div className="ch-store-name">{store.name}</div>
          <div className="ch-store-meta">
            {store.platform} · {store.plan}
          </div>
        </div>
      </div>
      <div className="ch-store-cells">
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Orders · 30d</div>
          <div className="ch-store-cell-value">{store.orders30d.toLocaleString()}</div>
        </div>
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Sync health</div>
          <div className="ch-store-cell-value">
            <div className="ch-health">
              <div
                className={
                  'ch-health-bar tone-' +
                  (store.health >= 90 ? 'success' : store.health >= 75 ? 'warning' : 'danger')
                }
                style={{ width: store.health + '%' }}
              />
            </div>
            <span className="ch-health-value">{store.health}%</span>
          </div>
        </div>
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Last sync</div>
          <div className="ch-store-cell-value muted">{store.lastSync}</div>
        </div>
      </div>
      <div className="ch-store-right">
        <span className={'ch-pill ch-pill-' + status.tone}>
          <span className="ch-pill-dot" /> {status.label}
        </span>
        <button className="btn secondary sm">Manage</button>
        <button className="ch-icon-btn" onClick={onDisconnect} title="Disconnect">
          <Icon.More />
        </button>
      </div>
    </div>
  );
}
