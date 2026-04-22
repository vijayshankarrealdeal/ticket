import { useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';

const initialPlatforms = [
  { id: 'whatsapp', name: 'WhatsApp', icon: 'W', cls: 'whatsapp', desc: 'Route chats from WhatsApp Business API', status: 'connected', tickets: 142 },
  { id: 'email', name: 'Email', icon: '✉', cls: 'mail', desc: 'Turn support emails into tickets', status: 'connected', tickets: 87 },
  { id: 'instagram', name: 'Instagram DM', icon: 'IG', cls: 'instagram', desc: 'Direct messages from Instagram', status: 'pending', tickets: 0 },
  { id: 'web', name: 'Web Widget', icon: '🌐', cls: 'web', desc: 'Embeddable chat widget on your site', status: 'connected', tickets: 211 },
  { id: 'slack', name: 'Slack', icon: '#', cls: 'slack', desc: 'Internal team channel escalations', status: 'connected', tickets: 34 },
  { id: 'telegram', name: 'Telegram', icon: '✈', cls: 'telegram', desc: 'Bot-based ticket creation', status: 'pending', tickets: 0 },
  { id: 'messenger', name: 'Messenger', icon: 'M', cls: 'messenger', desc: 'Facebook Messenger inbox', status: 'disconnected', tickets: 0 },
  { id: 'discord', name: 'Discord', icon: 'D', cls: 'discord', desc: 'Community server support', status: 'disconnected', tickets: 0 },
];

export default function Platforms() {
  const [platforms, setPlatforms] = useState(initialPlatforms);

  const toggle = (id) =>
    setPlatforms((prev) =>
      prev.map((p) =>
        p.id === id
          ? { ...p, status: p.status === 'connected' ? 'disconnected' : 'connected' }
          : p,
      ),
    );

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
            <div className="value">{platforms.filter((p) => p.status === 'connected').length}</div>
            <div className="trend">+2 this month</div>
          </div>
          <div className="stat">
            <div className="label">Tickets today</div>
            <div className="value">{platforms.reduce((s, p) => s + p.tickets, 0)}</div>
            <div className="trend">+12% vs yesterday</div>
          </div>
          <div className="stat">
            <div className="label">Pending setup</div>
            <div className="value">{platforms.filter((p) => p.status === 'pending').length}</div>
          </div>
          <div className="stat">
            <div className="label">Avg response</div>
            <div className="value">2m 14s</div>
            <div className="trend" style={{ color: 'var(--success)' }}>-18s</div>
          </div>
        </div>

        <div className="grid">
          {platforms.map((p) => (
            <div key={p.id} className="card">
              <div className="platform-head">
                <div className={'platform-icon ' + p.cls}>{p.icon}</div>
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
                  {p.tickets} tickets
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
