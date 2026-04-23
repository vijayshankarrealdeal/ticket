import { useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { useAnimationPref } from '../hooks/useAnimationPref.js';

const sections = [
  { key: 'profile', label: 'Profile' },
  { key: 'workspace', label: 'Workspace' },
  { key: 'preferences', label: 'Preferences' },
  { key: 'notifications', label: 'Notifications' },
  { key: 'security', label: 'Security' },
  { key: 'billing', label: 'Billing' },
];

export default function Settings() {
  const [active, setActive] = useState('profile');

  return (
    <>
      <TopBar left={<div className="top-bar-title">Settings</div>} />
      <div className="page-scroll">
        <div className="settings-layout">
          <div className="settings-nav">
            {sections.map((s) => (
              <button
                key={s.key}
                className={active === s.key ? 'active' : ''}
                onClick={() => setActive(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="card">
            {active === 'profile' && <Profile />}
            {active === 'workspace' && <Workspace />}
            {active === 'preferences' && <Preferences />}
            {active === 'notifications' && <Notifications />}
            {active === 'security' && <Security />}
            {active === 'billing' && <Billing />}
          </div>
        </div>
      </div>
    </>
  );
}

function Profile() {
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Profile</h3>
      <div className="form-group"><label>Name</label><input defaultValue="Jane Doe" /></div>
      <div className="form-group"><label>Email</label><input defaultValue="jane@thesleepcompany.in" /></div>
      <div className="form-group"><label>Role</label>
        <select defaultValue="admin">
          <option value="admin">Admin</option>
          <option value="agent">Agent</option>
          <option value="viewer">Viewer</option>
        </select>
      </div>
      <button className="btn">Save changes</button>
    </>
  );
}

function Workspace() {
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Workspace</h3>
      <div className="form-group"><label>Workspace name</label><input defaultValue="The Sleep Company" /></div>
      <div className="form-group"><label>Timezone</label>
        <select defaultValue="Asia/Kolkata">
          <option>Asia/Kolkata</option>
          <option>America/New_York</option>
          <option>Europe/London</option>
          <option>Asia/Tokyo</option>
        </select>
      </div>
      <div className="form-group"><label>Default language</label>
        <select defaultValue="en">
          <option value="en">English</option>
          <option value="hi">Hindi</option>
          <option value="es">Spanish</option>
        </select>
      </div>
      <button className="btn">Save changes</button>
    </>
  );
}

function Preferences() {
  const { level, setLevel } = useAnimationPref();
  const options = [
    {
      key: 'full',
      title: 'Full animations',
      desc: 'Smooth expand/collapse, panel slides, and micro-interactions everywhere.',
    },
    {
      key: 'subtle',
      title: 'Subtle',
      desc: 'Quick, minimal motion. Skips entrance/exit animations; keeps hover and toggles snappy.',
    },
    {
      key: 'off',
      title: 'Off',
      desc: 'No animations or transitions. UI elements open and close instantly.',
    },
  ];
  return (
    <>
      <h3 style={{ marginBottom: 6, fontSize: 16 }}>Preferences</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16, fontSize: 13 }}>
        Control how UI elements expand, collapse, and transition across the app.
      </p>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>Animations</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {options.map((opt) => {
          const selected = level === opt.key;
          return (
            <label
              key={opt.key}
              className={'pref-option' + (selected ? ' selected' : '')}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                padding: '12px 14px',
                border: '1px solid ' + (selected ? 'var(--accent, #1e40af)' : 'var(--border)'),
                borderRadius: 10,
                cursor: 'pointer',
                background: selected ? 'var(--hover)' : 'transparent',
              }}
            >
              <input
                type="radio"
                name="animation-level"
                value={opt.key}
                checked={selected}
                onChange={() => setLevel(opt.key)}
                style={{ marginTop: 3 }}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{opt.title}</span>
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>{opt.desc}</span>
              </div>
            </label>
          );
        })}
      </div>
      <p style={{ color: 'var(--muted)', marginTop: 14, fontSize: 12 }}>
        Changes apply immediately and are saved to this browser.
      </p>
    </>
  );
}

function Notifications() {
  const [email, setEmail] = useState(true);
  const [push, setPush] = useState(false);
  const [digest, setDigest] = useState(true);
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Notifications</h3>
      <Toggle label="Email alerts for new tickets" on={email} onChange={() => setEmail(!email)} />
      <Toggle label="Browser push notifications" on={push} onChange={() => setPush(!push)} />
      <Toggle label="Daily digest" on={digest} onChange={() => setDigest(!digest)} />
    </>
  );
}

function Security() {
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Security</h3>
      <div className="form-group"><label>Current password</label><input type="password" /></div>
      <div className="form-group"><label>New password</label><input type="password" /></div>
      <div className="form-group"><label>Confirm new password</label><input type="password" /></div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn">Update password</button>
        <button className="btn secondary">Enable 2FA</button>
      </div>
    </>
  );
}

function Billing() {
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Billing</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16 }}>
        You are on the <strong style={{ color: 'var(--text)' }}>Growth</strong> plan — $49/month.
      </p>
      <div className="stat-row" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="stat"><div className="label">Tickets this month</div><div className="value">1,284</div></div>
        <div className="stat"><div className="label">Active agents</div><div className="value">4</div></div>
        <div className="stat"><div className="label">Next invoice</div><div className="value">May 22</div></div>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button className="btn">Upgrade plan</button>
        <button className="btn secondary">Download invoices</button>
      </div>
    </>
  );
}

function Toggle({ label, on, onChange }) {
  return (
    <div className="tool-row">
      <span style={{ fontWeight: 500 }}>{label}</span>
      <div className={'switch' + (on ? ' on' : '')} onClick={onChange} />
    </div>
  );
}
