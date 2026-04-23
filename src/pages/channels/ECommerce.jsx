import { useEffect, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { api } from '../../api.js';

const EVENT_ICONS = {
  order: { icon: Icon.ShoppingBag, tone: 'primary' },
  sync: { icon: Icon.Refresh, tone: 'info' },
  issue: { icon: Icon.AlertTriangle, tone: 'warning' },
  refund: { icon: Icon.Refresh, tone: 'danger' },
};

export default function ECommerce() {
  const [stores, setStores] = useState([]);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(null);

  async function refresh() {
    const [s, e] = await Promise.all([
      api.get('/ecommerce/stores').catch(() => []),
      api.get('/ecommerce/events').catch(() => []),
    ]);
    setStores(Array.isArray(s) ? s : s?.items || []);
    setEvents(Array.isArray(e) ? e : e?.items || []);
  }

  useEffect(() => { refresh(); }, []);

  const disconnect = async (id) => {
    if (!confirm('Disconnect this store?')) return;
    try { await api.del(`/ecommerce/stores/${encodeURIComponent(id)}`); } catch {}
    setStores((prev) => prev.filter((s) => s.id !== id));
  };

  const syncStore = async (id) => {
    setBusy(id);
    try {
      await api.post(`/ecommerce/stores/${encodeURIComponent(id)}/sync`);
      await refresh();
    } catch (e) {
      alert(`Sync failed: ${e.message || e}`);
    } finally {
      setBusy(null);
    }
  };

  const syncAll = async () => {
    setBusy('all');
    try {
      await Promise.all(stores.map((s) => api.post(`/ecommerce/stores/${encodeURIComponent(s.id)}/sync`).catch(() => {})));
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const addStore = async () => {
    const name = prompt('Store name?');
    if (!name) return;
    const platform = prompt('Platform? (shopify / unicommerce)', 'shopify');
    try {
      await api.post('/ecommerce/stores', { name, platform: platform || 'shopify' });
      await refresh();
    } catch (e) {
      alert(`Add failed: ${e.message || e}`);
    }
  };

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
          <button className="btn secondary" onClick={syncAll} disabled={busy === 'all'}>
            <Icon.Refresh /> {busy === 'all' ? 'Syncing…' : 'Sync all'}
          </button>
          <button className="btn" onClick={addStore}><Icon.Plus /> Add store</button>
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
              <StoreRow
                key={s.id}
                store={s}
                onDisconnect={() => disconnect(s.id)}
                onSync={() => syncStore(s.id)}
                syncing={busy === s.id}
              />
            ))}
            {stores.length === 0 && (
              <div className="ch-empty inline">
                <div className="ch-empty-title">No stores connected</div>
                <div className="ch-empty-sub">Click "Add store" to connect one.</div>
              </div>
            )}
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
            {events.map((e) => {
              const meta = EVENT_ICONS[e.type] || { icon: Icon.Activity, tone: 'muted' };
              const I = meta.icon;
              return (
                <div key={e.id} className="ch-activity-item">
                  <div className={'ch-activity-icon tone-' + (e.tone || meta.tone)}>
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

function StoreRow({ store, onDisconnect, onSync, syncing }) {
  const statusMap = {
    connected: { label: 'Connected', tone: 'success' },
    syncing: { label: 'Syncing', tone: 'info' },
    attention: { label: 'Needs attention', tone: 'warning' },
  };
  const status = statusMap[store.status] || { label: store.status || 'Unknown', tone: 'muted' };
  const accent = store.accent || (store.config && store.config.accent) || '#7c3aed';
  const letter = store.letter || (store.name || '?').slice(0, 1).toUpperCase();
  const plan = store.plan || (store.config && store.config.plan) || '—';
  const orders30d = store.orders30d ?? 0;
  const health = store.health ?? 100;
  return (
    <div className="ch-store-row">
      <div className="ch-store-left">
        <div className="ch-card-logo" style={{ background: accent }}>
          {letter}
        </div>
        <div>
          <div className="ch-store-name">{store.name}</div>
          <div className="ch-store-meta">
            {store.platform} · {plan}
          </div>
        </div>
      </div>
      <div className="ch-store-cells">
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Orders · 30d</div>
          <div className="ch-store-cell-value">{(orders30d || 0).toLocaleString()}</div>
        </div>
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Sync health</div>
          <div className="ch-store-cell-value">
            <div className="ch-health">
              <div
                className={
                  'ch-health-bar tone-' +
                  (health >= 90 ? 'success' : health >= 75 ? 'warning' : 'danger')
                }
                style={{ width: health + '%' }}
              />
            </div>
            <span className="ch-health-value">{health}%</span>
          </div>
        </div>
        <div className="ch-store-cell">
          <div className="ch-store-cell-label">Last sync</div>
          <div className="ch-store-cell-value muted">{store.lastSync || '—'}</div>
        </div>
      </div>
      <div className="ch-store-right">
        <span className={'ch-pill ch-pill-' + status.tone}>
          <span className="ch-pill-dot" /> {status.label}
        </span>
        <button className="btn secondary sm" onClick={onSync} disabled={syncing}>
          {syncing ? 'Syncing…' : 'Sync'}
        </button>
        <button className="ch-icon-btn" onClick={onDisconnect} title="Disconnect">
          <Icon.Trash />
        </button>
      </div>
    </div>
  );
}
