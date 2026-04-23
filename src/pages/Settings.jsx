import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { useAnimationPref } from '../hooks/useAnimationPref.js';
import { api } from '../api.js';

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

function useAsyncSave() {
  const [state, setState] = useState({ status: 'idle', error: null });
  async function run(fn) {
    setState({ status: 'saving', error: null });
    try {
      await fn();
      setState({ status: 'saved', error: null });
      setTimeout(() => setState((s) => (s.status === 'saved' ? { status: 'idle', error: null } : s)), 1500);
    } catch (e) {
      setState({ status: 'error', error: String(e?.message || e) });
    }
  }
  return [state, run];
}

function SaveButton({ state, label = 'Save changes' }) {
  const { status, error } = state;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <button type="submit" className="btn" disabled={status === 'saving'}>
        {status === 'saving' ? 'Saving…' : label}
      </button>
      {status === 'saved' && <span style={{ color: 'var(--success, #059669)', fontSize: 13 }}>Saved</span>}
      {status === 'error' && <span style={{ color: 'var(--danger, #b91c1c)', fontSize: 13 }}>{error}</span>}
    </div>
  );
}

function Profile() {
  const [me, setMe] = useState(null);
  const [state, save] = useAsyncSave();
  useEffect(() => { api.get('/me').then(setMe).catch(() => {}); }, []);
  function onSubmit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    save(async () => {
      const updated = await api.patch('/me', { name: form.get('name') });
      setMe(updated);
    });
  }
  if (!me) return <div style={{ color: 'var(--muted)' }}>Loading…</div>;
  return (
    <form onSubmit={onSubmit}>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Profile</h3>
      <div className="form-group"><label>Name</label><input name="name" defaultValue={me.name} /></div>
      <div className="form-group"><label>Email</label><input defaultValue={me.email} disabled /></div>
      <div className="form-group"><label>Role</label>
        <select defaultValue={me.role} disabled>
          <option value="admin">Admin</option>
          <option value="agent">Agent</option>
          <option value="viewer">Viewer</option>
        </select>
      </div>
      <SaveButton state={state} />
    </form>
  );
}

function Workspace() {
  const [ws, setWs] = useState(null);
  const [state, save] = useAsyncSave();
  useEffect(() => { api.get('/workspace').then(setWs).catch(() => {}); }, []);
  function onSubmit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    save(async () => {
      const updated = await api.patch('/workspace', {
        name: form.get('name'),
        timezone: form.get('timezone'),
        default_lang: form.get('lang'),
      });
      setWs(updated);
    });
  }
  if (!ws) return <div style={{ color: 'var(--muted)' }}>Loading…</div>;
  return (
    <form onSubmit={onSubmit}>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Workspace</h3>
      <div className="form-group"><label>Workspace name</label><input name="name" defaultValue={ws.name} /></div>
      <div className="form-group"><label>Timezone</label>
        <select name="timezone" defaultValue={ws.timezone || 'Asia/Kolkata'}>
          <option>Asia/Kolkata</option>
          <option>America/New_York</option>
          <option>Europe/London</option>
          <option>Asia/Tokyo</option>
        </select>
      </div>
      <div className="form-group"><label>Default language</label>
        <select name="lang" defaultValue={ws.default_lang || 'en'}>
          <option value="en">English</option>
          <option value="hi">Hindi</option>
          <option value="es">Spanish</option>
        </select>
      </div>
      <SaveButton state={state} />
    </form>
  );
}

function Preferences() {
  const { level, setLevel } = useAnimationPref();
  const options = [
    { key: 'full', title: 'Full animations', desc: 'Smooth expand/collapse, panel slides, and micro-interactions everywhere.' },
    { key: 'subtle', title: 'Subtle', desc: 'Quick, minimal motion. Skips entrance/exit animations; keeps hover and toggles snappy.' },
    { key: 'off', title: 'Off', desc: 'No animations or transitions. UI elements open and close instantly.' },
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
                display: 'flex', alignItems: 'flex-start', gap: 12,
                padding: '12px 14px',
                border: '1px solid ' + (selected ? 'var(--accent, #1e40af)' : 'var(--border)'),
                borderRadius: 10, cursor: 'pointer',
                background: selected ? 'var(--hover)' : 'transparent',
              }}
            >
              <input type="radio" name="animation-level" value={opt.key}
                     checked={selected} onChange={() => setLevel(opt.key)} style={{ marginTop: 3 }} />
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
  const [prefs, setPrefs] = useState({ email: true, browser: false, digest: 'daily' });
  const [state, save] = useAsyncSave();
  useEffect(() => {
    api.get('/me/notification-prefs').then((p) => setPrefs({ ...prefs, ...p })).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function toggle(key) {
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    save(() => api.patch('/me/notification-prefs', { [key]: next[key] }));
  }
  function setDigest(v) {
    const next = { ...prefs, digest: v };
    setPrefs(next);
    save(() => api.patch('/me/notification-prefs', { digest: v }));
  }
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Notifications</h3>
      <Toggle label="Email alerts for new tickets" on={!!prefs.email} onChange={() => toggle('email')} />
      <Toggle label="Browser push notifications" on={!!prefs.browser} onChange={() => toggle('browser')} />
      <div className="form-group" style={{ marginTop: 10 }}>
        <label>Digest</label>
        <select value={prefs.digest || 'daily'} onChange={(e) => setDigest(e.target.value)}>
          <option value="off">Off</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </div>
      {state.status === 'saved' && <span style={{ color: 'var(--success, #059669)', fontSize: 13 }}>Saved</span>}
      {state.status === 'error' && <span style={{ color: 'var(--danger, #b91c1c)', fontSize: 13 }}>{state.error}</span>}
    </>
  );
}

function Security() {
  const [state, save] = useAsyncSave();
  function onSubmit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const current = form.get('current');
    const next = form.get('new');
    const confirm = form.get('confirm');
    if (next !== confirm) {
      save(async () => { throw new Error('passwords do not match'); });
      return;
    }
    save(async () => {
      await api.post('/me/password', { current, new: next });
      e.currentTarget.reset();
    });
  }
  return (
    <form onSubmit={onSubmit}>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Security</h3>
      <div className="form-group"><label>Current password</label><input name="current" type="password" /></div>
      <div className="form-group"><label>New password</label><input name="new" type="password" /></div>
      <div className="form-group"><label>Confirm new password</label><input name="confirm" type="password" /></div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <SaveButton state={state} label="Update password" />
        <button type="button" className="btn secondary" disabled>Enable 2FA</button>
      </div>
    </form>
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
        <button className="btn" disabled>Upgrade plan</button>
        <button className="btn secondary" disabled>Download invoices</button>
      </div>
      <p style={{ color: 'var(--muted)', marginTop: 16, fontSize: 12 }}>
        Stripe wiring lands in Build 4 — see BACKEND_SPEC.md §3.12.
      </p>
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
