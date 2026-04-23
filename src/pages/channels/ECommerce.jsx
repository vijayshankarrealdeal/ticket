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
  const [stats, setStats] = useState(null);
  const [busy, setBusy] = useState(null);
  const [toast, setToast] = useState(null);

  const flash = (msg, tone = 'success') => {
    setToast({ msg, tone });
    setTimeout(() => setToast((t) => (t && t.msg === msg ? null : t)), 4000);
  };

  async function refresh() {
    const [s, e, st] = await Promise.all([
      api.get('/ecommerce/stores').catch(() => []),
      api.get('/ecommerce/events').catch(() => []),
      api.get('/ecommerce/stats').catch(() => null),
    ]);
    setStores(Array.isArray(s) ? s : s?.items || []);
    setEvents(Array.isArray(e) ? e : e?.items || []);
    setStats(st && typeof st === 'object' ? st : null);
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
      const res = await api.post(`/ecommerce/stores/${encodeURIComponent(id)}/sync`);
      await refresh();
      if (res?.error) {
        flash(`Sync warning: ${res.error}`, 'warning');
      } else if (res?.fallback) {
        flash('Synced (demo mode — connect Shopify credentials to pull real orders)', 'info');
      } else {
        const pulled = res?.ordersPulled ?? 0;
        const fresh = res?.newOrders ?? 0;
        flash(`Pulled ${pulled} order${pulled === 1 ? '' : 's'} (${fresh} new)`, 'success');
      }
    } catch (e) {
      flash(`Sync failed: ${e.message || e}`, 'danger');
    } finally {
      setBusy(null);
    }
  };

  const syncAll = async () => {
    setBusy('all');
    try {
      const results = await Promise.all(stores.map((s) =>
        api.post(`/ecommerce/stores/${encodeURIComponent(s.id)}/sync`)
          .catch((e) => ({ error: e.message || String(e) }))
      ));
      await refresh();
      const totalPulled = results.reduce((n, r) => n + (r?.ordersPulled || 0), 0);
      const errors = results.filter((r) => r?.error).length;
      if (errors) flash(`Synced ${stores.length - errors}/${stores.length} stores (${errors} error${errors === 1 ? '' : 's'})`, 'warning');
      else flash(`Synced ${stores.length} store${stores.length === 1 ? '' : 's'} · ${totalPulled} order${totalPulled === 1 ? '' : 's'}`, 'success');
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
      {toast && (
        <div
          className={`ch-toast ch-toast-${toast.tone}`}
          role="status"
          onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}
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
        <Kpi
          label="Total orders · 30d"
          value={stats ? (stats.totalOrders30d ?? 0).toLocaleString() : '—'}
        />
        <Kpi
          label="Active stores"
          value={stats ? stats.activeStores : stores.length}
          delta={stats && stats.newStoresThisMonth > 0 ? `+${stats.newStoresThisMonth} this month` : null}
          tone="success"
        />
        <Kpi
          label="Avg sync latency"
          value={stats && stats.avgSyncLatencyMs != null ? `${stats.avgSyncLatencyMs}ms` : '—'}
        />
        <Kpi
          label="Failed webhooks"
          value={stats ? stats.failedWebhooks : 0}
          delta={
            stats && stats.failedWebhooksDelta
              ? (stats.failedWebhooksDelta > 0 ? `+${stats.failedWebhooksDelta}` : `${stats.failedWebhooksDelta}`)
              : null
          }
          tone={stats && stats.failedWebhooks > 0 ? 'warning' : 'muted'}
        />
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
      {delta ? <div className={'ch-kpi-delta tone-' + tone}>{delta}</div> : null}
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
