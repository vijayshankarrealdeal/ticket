import { useEffect, useMemo, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { Spinner } from '../components/Spinner.jsx';
import { api } from '../api.js';

const GROUP_LABELS = {
  messaging: 'Messaging & Email',
  ecommerce: 'E-commerce',
  shipping: 'Shipping & logistics',
  payments: 'Payments',
};

// Channels that drive OAuth when Connect is clicked. `provider` is the
// backend /auth/oauth/<provider>/start slug — Gmail uses the Google flow, the
// rest reuse the provider's own start endpoint (not wired today for IG /
// Messenger, so those still fall through to the stub manage modal).
const OAUTH_CHANNELS = {
  gmail: 'google',
  outlook: 'outlook',
};

export default function Platforms() {
  const [platforms, setPlatforms] = useState([]);
  const [stats, setStats] = useState(null);
  const [manage, setManage] = useState(null); // the platform being managed
  const [busy, setBusy] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);

  async function refresh() {
    const [list, s] = await Promise.all([
      api.get('/platforms?include=all').catch(() => []),
      api.get('/platforms/stats').catch(() => null),
    ]);
    setPlatforms(Array.isArray(list) ? list : list?.items || []);
    setStats(s || null);
  }

  useEffect(() => {
    refresh().finally(() => setInitialLoading(false));
  }, []);

  // OAuth return: the backend sends us back with ?connected=<provider>, so
  // surface a quick confirmation and scrub the query param.
  const [flash, setFlash] = useState(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    if (connected) {
      setFlash(`${connected} connected — new inbound messages will flow into this workspace.`);
      params.delete('connected');
      const next = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (next ? `?${next}` : ''));
      setTimeout(() => setFlash(null), 6000);
    }
  }, []);

  const grouped = useMemo(() => {
    const g = new Map();
    platforms.forEach((p) => {
      const key = p._group || p.group || p.kind || 'other';
      if (!g.has(key)) g.set(key, []);
      g.get(key).push(p);
    });
    return g;
  }, [platforms]);

  async function toggle(p) {
    const connected = p.status === 'connected';
    if (!connected && OAUTH_CHANNELS[p.id]) {
      // Real OAuth round-trip: redirect to the backend starter, which bounces
      // to the provider and drops us back at /platforms with ?connected=<id>.
      // Same-origin path uses the wx_session cookie. For cross-origin dev
      // (SPA on :5173 → backend on :8000) we fall back to a short-lived
      // handoff token minted from the cookie — the long-lived bearer is
      // never read from JS-accessible storage.
      const base = import.meta.env.VITE_API_URL || '/api';
      const { getHandoffToken } = await import('../api.js');
      const token = (await getHandoffToken()) || '';
      const returnTo = window.location.origin + window.location.pathname;
      const params = { mode: 'connect', return_to: returnTo };
      if (token) params.token = token;
      const qs = new URLSearchParams(params).toString();
      window.location.href = `${base}/auth/oauth/${OAUTH_CHANNELS[p.id]}/start?${qs}`;
      return;
    }
    setBusy(p.id);
    try {
      const endpoint = connected ? 'disconnect' : 'connect';
      await api.post(`/platforms/${encodeURIComponent(p.id)}/${endpoint}`);
      setPlatforms((prev) =>
        prev.map((q) =>
          q.id === p.id
            ? { ...q, status: connected ? 'available' : 'connected' }
            : q,
        ),
      );
      // Refresh stats after status change
      api.get('/platforms/stats').then((s) => setStats(s)).catch(() => {});
    } catch (e) {
      alert(`Failed to ${connected ? 'disconnect' : 'connect'}: ${e.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  const connectedCount = stats?.connected ?? platforms.filter((p) => p.status === 'connected').length;
  const ticketsToday = stats?.ticketsToday ?? platforms.reduce((s, p) => s + (p.tickets || 0), 0);
  const pendingCount = stats?.pending ?? platforms.filter((p) => p.status === 'pending').length;
  const avgResponse = stats?.avgResponse ?? '—';

  return (
    <>
      <TopBar left={<div className="top-bar-title">Platforms</div>} />
      <div className="page-scroll">
        <div className="page-header">
          <div>
            <div className="page-title">Channels</div>
            <div className="page-sub">Connect every channel where your customers reach you.</div>
          </div>
        </div>

        {flash && (
          <div style={{ background: 'var(--success-soft, #ecfdf5)',
                        border: '1px solid var(--success, #059669)',
                        color: 'var(--success, #047857)', padding: '10px 14px',
                        borderRadius: 10, marginBottom: 14, fontSize: 13 }}>
            {flash}
          </div>
        )}

        {initialLoading && platforms.length === 0 ? (
          <div style={{ padding: '80px 16px', display: 'flex', justifyContent: 'center' }}>
            <Spinner size={32} />
          </div>
        ) : (
        <>
        <div className="stat-row">
          <div className="stat">
            <div className="label">Connected</div>
            <div className="value">{connectedCount}</div>
            {stats?.connectedTrend && <div className="trend">{stats.connectedTrend}</div>}
          </div>
          <div className="stat">
            <div className="label">Tickets today</div>
            <div className="value">{ticketsToday}</div>
            {stats?.ticketsTrend && <div className="trend">{stats.ticketsTrend}</div>}
          </div>
          <div className="stat">
            <div className="label">Pending setup</div>
            <div className="value">{pendingCount}</div>
          </div>
          <div className="stat">
            <div className="label">Avg response</div>
            <div className="value">{avgResponse}</div>
            {stats?.avgResponseTrend && (
              <div className="trend" style={{ color: 'var(--success)' }}>{stats.avgResponseTrend}</div>
            )}
          </div>
        </div>

        {Array.from(grouped.entries()).map(([group, items]) => (
          <div key={group} style={{ marginTop: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', margin: '4px 0 10px' }}>
              {GROUP_LABELS[group] || group}
            </div>
            <div className="grid">
              {items.map((p) => (
                <div key={p.id} className="card">
                  <div className="platform-head">
                    <div
                      className={'platform-icon ' + (p.cls || '')}
                      style={p.accent ? { background: p.accent, color: '#fff' } : undefined}
                    >
                      {p.letter || (p.name || '?')[0]}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 15 }}>{p.name}</div>
                      <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>{p.desc}</div>
                    </div>
                  </div>

                  <div className="platform-meta">
                    <span className={
                      'status-badge ' +
                      (p.status === 'connected' ? 'status-resolved'
                        : p.status === 'pending' ? 'status-queued' : 'status-closed')
                    }>
                      ● {p.status === 'connected'
                        ? 'Connected'
                        : p.status === 'pending'
                          ? 'Pending'
                          : p.status === 'available'
                            ? 'Available'
                            : 'Disconnected'}
                    </span>
                    {p.needs_reauth && (
                      <span
                        className="status-badge status-queued"
                        title="The stored access token can't be refreshed. Click Manage to reconnect."
                        style={{ fontSize: 11 }}
                      >
                        ● Reauth needed
                      </span>
                    )}
                    <span style={{ color: 'var(--muted)', fontSize: 12, fontWeight: 500 }}>
                      {p.tickets ?? 0} tickets
                    </span>
                  </div>

                  <button
                    className={p.status === 'connected' ? 'btn secondary' : 'btn'}
                    style={{ width: '100%', justifyContent: 'center' }}
                    onClick={() => p.status === 'connected' ? setManage(p) : toggle(p)}
                    disabled={busy === p.id}
                  >
                    {busy === p.id
                      ? 'Working…'
                      : p.status === 'connected'
                        ? 'Manage'
                        : 'Connect'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))}
        </>
        )}
      </div>

      {manage && (
        <ManagePlatformModal
          platform={manage}
          onClose={() => setManage(null)}
          onDisconnect={async () => {
            setBusy(manage.id);
            try {
              await api.post(`/platforms/${encodeURIComponent(manage.id)}/disconnect`);
              setPlatforms((prev) => prev.map((q) => q.id === manage.id ? { ...q, status: 'available' } : q));
              setManage(null);
            } finally {
              setBusy(null);
            }
          }}
          onConnect={async () => {
            setBusy(manage.id);
            try {
              await api.post(`/platforms/${encodeURIComponent(manage.id)}/connect`);
              setPlatforms((prev) => prev.map((q) => q.id === manage.id ? { ...q, status: 'connected' } : q));
              setManage(null);
            } finally {
              setBusy(null);
            }
          }}
          busy={busy === manage.id}
        />
      )}
    </>
  );
}

function ManagePlatformModal({ platform, onClose, onConnect, onDisconnect, busy }) {
  const isConnected = platform.status === 'connected';
  const isOAuth = Boolean(OAUTH_CHANNELS[platform.id]);
  const [sync, setSync] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const canSync = isConnected && platform.id === 'gmail';
  const runSync = async () => {
    setSyncing(true); setSync(null);
    try {
      const r = await api.post('/platforms/gmail/sync');
      setSync(`Pulled ${r?.ingested ?? 0} new / ${r?.fetched ?? 0} unread across ${r?.accounts ?? 0} account(s).`);
    } catch (e) {
      setSync(`Sync failed: ${e?.message || e}`);
    } finally { setSyncing(false); }
  };
  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--card, #fff)', borderRadius: 12, padding: 20, width: 480,
          maxWidth: '94%', boxShadow: '0 20px 50px rgba(15,23,42,0.25)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <div
            className={'platform-icon ' + (platform.cls || '')}
            style={platform.accent ? { background: platform.accent, color: '#fff', width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700 } : undefined}
          >
            {platform.letter || platform.name[0]}
          </div>
          <h3 style={{ margin: 0 }}>{platform.name}</h3>
        </div>
        <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 14 }}>{platform.desc}</div>

        {isConnected ? (
          <div style={{ background: 'var(--bg-soft, #f8fafc)', padding: 12, borderRadius: 8, fontSize: 13 }}>
            This channel is connected. New conversations will appear in Conversations as they arrive.
            {isOAuth && (
              <div style={{ marginTop: 8, color: 'var(--muted)', fontSize: 12 }}>
                Signed in via {OAUTH_CHANNELS[platform.id]}. Disconnecting revokes the stored
                credentials; inbound messages will stop until you reconnect.
              </div>
            )}
            {canSync && (
              <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <button className="btn secondary sm" disabled={syncing} onClick={runSync}>
                  {syncing ? 'Syncing…' : 'Sync inbox now'}
                </button>
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                  Background poll runs every 30 s.
                </span>
                {sync && <div style={{ flexBasis: '100%', color: 'var(--muted)', fontSize: 12 }}>{sync}</div>}
              </div>
            )}
          </div>
        ) : (
          <div style={{ background: 'var(--bg-soft, #f8fafc)', padding: 12, borderRadius: 8, fontSize: 13 }}>
            Connect <b>{platform.name}</b> to start routing its conversations into your inbox.
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button className="btn secondary" onClick={onClose} disabled={busy}>Close</button>
          {isConnected ? (
            <button className="btn" onClick={onDisconnect} disabled={busy}>
              {busy ? 'Disconnecting…' : 'Disconnect'}
            </button>
          ) : (
            <button className="btn" onClick={onConnect} disabled={busy}>
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
