import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { api } from '../api.js';

export default function Platforms() {
  const [platforms, setPlatforms] = useState([]);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.get('/platforms').catch(() => []),
      api.get('/platforms/stats').catch(() => null),
    ]).then(([list, s]) => {
      if (!alive) return;
      setPlatforms(Array.isArray(list) ? list : list?.items || []);
      setStats(s || null);
    });
    return () => { alive = false; };
  }, []);

  const toggle = async (id) => {
    const p = platforms.find((x) => x.id === id);
    if (!p) return;
    const endpoint = p.status === 'connected' ? 'disconnect' : 'connect';
    try {
      await api.post(`/platforms/${encodeURIComponent(id)}/${endpoint}`);
    } catch { /* no-op */ }
    setPlatforms((prev) =>
      prev.map((q) =>
        q.id === id
          ? { ...q, status: q.status === 'connected' ? 'disconnected' : 'connected' }
          : q,
      ),
    );
  };

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
          <button className="btn"><Icon.Plus /> Add platform</button>
        </div>

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

        <div className="grid">
          {platforms.map((p) => (
            <div key={p.id} className="card">
              <div className="platform-head">
                <div className={'platform-icon ' + (p.cls || '')}>{p.icon}</div>
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
                  ● {p.status === 'connected' ? 'Connected' : p.status === 'pending' ? 'Pending' : 'Disconnected'}
                </span>
                <span style={{ color: 'var(--muted)', fontSize: 12, fontWeight: 500 }}>
                  {p.tickets ?? 0} tickets
                </span>
              </div>

              <button
                className={p.status === 'connected' ? 'btn secondary' : 'btn'}
                style={{ width: '100%', justifyContent: 'center' }}
                onClick={() => toggle(p.id)}
              >
                {p.status === 'connected' ? 'Manage' : 'Connect'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
