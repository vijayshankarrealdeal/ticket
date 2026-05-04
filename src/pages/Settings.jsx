import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { useAnimationPref } from '../hooks/useAnimationPref.js';
import { api } from '../api.js';

// Surface fetch failures in devtools instead of silently rendering empty
// states. The settings page is full of optional sub-resources (billing, SLA,
// notification prefs) that legitimately may not exist; we still want the
// underlying error visible when one of them blows up.
const logError = (scope) => (err) => console.error(`[settings:${scope}]`, err);

const sections = [
  { key: 'profile', label: 'Profile' },
  { key: 'workspace', label: 'Workspace' },
  { key: 'members', label: 'Members' },
  { key: 'preferences', label: 'Preferences' },
  { key: 'notifications', label: 'Notifications' },
  { key: 'broadcasts', label: 'Broadcasts' },
  { key: 'sla', label: 'SLA policies' },
  { key: 'deliveries', label: 'Deliveries' },
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
            {active === 'members' && <Members />}
            {active === 'preferences' && <Preferences />}
            {active === 'notifications' && <Notifications />}
            {active === 'broadcasts' && <Broadcasts />}
            {active === 'sla' && <SlaPolicies />}
            {active === 'deliveries' && <Deliveries />}
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
  useEffect(() => { api.get('/me').then(setMe).catch(logError('profile')); }, []);
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
  useEffect(() => { api.get('/workspace').then(setWs).catch(logError('workspace')); }, []);
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
    api.get('/me/notification-prefs').then((p) => setPrefs({ ...prefs, ...p })).catch(logError('notification-prefs'));
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
    <div>
      <form onSubmit={onSubmit}>
        <h3 style={{ marginBottom: 14, fontSize: 16 }}>Security</h3>
        <div className="form-group"><label>Current password</label><input name="current" type="password" /></div>
        <div className="form-group"><label>New password</label><input name="new" type="password" /></div>
        <div className="form-group"><label>Confirm new password</label><input name="confirm" type="password" /></div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <SaveButton state={state} label="Update password" />
        </div>
      </form>
      <TwoFactor />
      <ConnectedAccounts />
    </div>
  );
}

function ConnectedAccounts() {
  // Shows the OAuth providers the user has linked and lets them unlink. The
  // backend blocks unlink when the user would lock themselves out (no
  // password hash AND currently on that provider — see DELETE /me/oauth/:p).
  const [me, setMe] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);

  const reload = () => api.get('/me').then(setMe).catch(logError('security'));
  useEffect(() => { reload(); }, []);

  const providers = [
    { id: 'google',  label: 'Google',   connectPath: '/api/auth/oauth/google/start?mode=connect' },
    { id: 'outlook', label: 'Microsoft', connectPath: '/api/auth/oauth/outlook/start?mode=connect' },
  ];

  async function unlink(provider) {
    if (!confirm(`Disconnect ${provider} from your account?`)) return;
    setBusy(provider); setErr(null); setMsg(null);
    try {
      await api.del(`/me/oauth/${provider}`);
      setMsg(`Disconnected from ${provider}`);
      await reload();
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  if (!me) return null;
  return (
    <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Connected accounts</div>
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
        OAuth providers linked to this user. You can sign in with any of them.
        To unlink your only provider, set a password first.
      </div>
      {err && <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
      {msg && <div style={{ color: 'var(--success, #059669)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {providers.map((p) => {
          const linked = me.provider === p.id;
          return (
            <div key={p.id} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 8,
            }}>
              <div>
                <div style={{ fontWeight: 500 }}>{p.label}</div>
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                  {linked ? `Linked · signed in via ${p.label}` : 'Not connected'}
                </div>
              </div>
              {linked ? (
                <button type="button" className="btn secondary"
                        disabled={busy === p.id}
                        onClick={() => unlink(p.id)}>
                  {busy === p.id ? 'Disconnecting…' : 'Disconnect'}
                </button>
              ) : (
                <a href={p.connectPath} className="btn secondary">Connect</a>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TwoFactor() {
  const [enabled, setEnabled] = useState(null);
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get('/me/2fa').then((r) => setEnabled(!!r?.enabled)).catch(() => setEnabled(false));
  }, []);

  async function start() {
    setBusy(true); setErr(null);
    try {
      const r = await api.post('/me/2fa/setup');
      setSetup(r);
    } catch (e) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }
  async function verify(e) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      await api.post('/me/2fa/verify', { code });
      setEnabled(true); setSetup(null); setCode('');
    } catch (e2) { setErr(String(e2?.message || e2)); }
    finally { setBusy(false); }
  }
  async function disable(e) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      await api.post('/me/2fa/disable', { code });
      setEnabled(false); setCode('');
    } catch (e2) { setErr(String(e2?.message || e2)); }
    finally { setBusy(false); }
  }

  if (enabled === null) return null;
  return (
    <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Two-factor authentication</div>
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
        TOTP using any standard authenticator app (1Password, Authy, Google Authenticator).
        Status: <strong style={{ color: enabled ? 'var(--success, #059669)' : 'var(--muted)' }}>
          {enabled ? 'Enabled' : 'Disabled'}
        </strong>
      </div>
      {err && <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
      {!enabled && !setup && (
        <button type="button" className="btn secondary" disabled={busy} onClick={start}>
          {busy ? 'Generating…' : 'Enable 2FA'}
        </button>
      )}
      {!enabled && setup && (
        <div>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 6 }}>
            Add this to your authenticator, then paste the 6-digit code:
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 13, padding: '8px 10px',
            background: 'var(--surface-2, #f6f7f9)', borderRadius: 6, marginBottom: 8,
            wordBreak: 'break-all' }}>{setup.secret}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 10 }}>
            otpauth: <code>{setup.otpauthUrl}</code>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={code} onChange={(e) => setCode(e.target.value)}
              placeholder="123456" maxLength={6}
              style={{ width: 120, fontFamily: 'monospace', letterSpacing: 2 }} />
            <button type="button" className="btn" disabled={busy} onClick={verify}>Verify & enable</button>
            <button type="button" className="btn secondary" disabled={busy}
              onClick={() => { setSetup(null); setCode(''); }}>Cancel</button>
          </div>
        </div>
      )}
      {enabled && (
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={code} onChange={(e) => setCode(e.target.value)}
            placeholder="123456" maxLength={6}
            style={{ width: 120, fontFamily: 'monospace', letterSpacing: 2 }} />
          <button type="button" className="btn secondary" disabled={busy} onClick={disable}>
            Disable 2FA
          </button>
        </div>
      )}
    </div>
  );
}

function SlaPolicies() {
  // Per-team / per-task SLA hours. The most-specific match wins when a ticket
  // is created (see backend/routers/me.py + tickets._sla_hours).
  const [me, setMe] = useState(null);
  const [list, setList] = useState([]);
  const [state, save] = useAsyncSave();
  const isAdmin = me?.role === 'admin';
  useEffect(() => {
    api.get('/me').then(setMe).catch(logError('sla-me'));
    api.get('/workspace/sla-policies').then(setList).catch((err) => {
      logError('sla-policies')(err);
      setList([]);
    });
  }, []);
  function onSubmit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const payload = {
      team: (form.get('team') || '').toString().trim(),
      task: (form.get('task') || '').toString().trim(),
      priority: (form.get('priority') || '').toString().trim(),
      hours: Number(form.get('hours') || 0),
    };
    if (!payload.hours) return;
    save(async () => {
      const created = await api.post('/workspace/sla-policies', payload);
      setList((p) => [created, ...p]);
      e.target.reset();
    });
  }
  async function remove(id) {
    if (!confirm('Delete this SLA policy?')) return;
    try { await api.del(`/workspace/sla-policies/${id}`); } catch {}
    setList((p) => p.filter((x) => x.id !== id));
  }
  return (
    <>
      <h3 style={{ marginBottom: 6, fontSize: 16 }}>SLA policies</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16, fontSize: 13 }}>
        Hours from ticket creation to SLA due. Most-specific match wins
        (team + task + priority &gt; team + task &gt; team &gt; default).
        Default is 24h when no policy matches.
      </p>
      {!isAdmin ? (
        <p style={{ color: 'var(--muted)', fontSize: 13 }}>Only admins can edit SLA policies.</p>
      ) : (
        <form onSubmit={onSubmit} style={{ display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr) auto', gap: 8, alignItems: 'end' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Team (optional)</label>
            <input name="team" placeholder="Support" />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Task (optional)</label>
            <input name="task" placeholder="refund" />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Priority (optional)</label>
            <input name="priority" placeholder="urgent" />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Hours</label>
            <input name="hours" type="number" step="0.5" placeholder="24" required />
          </div>
          <SaveButton state={state} label="Add policy" />
        </form>
      )}
      <div style={{ marginTop: 20 }}>
        {list.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>No policies — falling back to 24h default.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '6px 4px' }}>Team</th>
                <th style={{ padding: '6px 4px' }}>Task</th>
                <th style={{ padding: '6px 4px' }}>Priority</th>
                <th style={{ padding: '6px 4px' }}>Hours</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px' }}>{p.team || '—'}</td>
                  <td style={{ padding: '8px 4px' }}>{p.task || '—'}</td>
                  <td style={{ padding: '8px 4px' }}>{p.priority || '—'}</td>
                  <td style={{ padding: '8px 4px', fontFamily: 'monospace' }}>{p.hours}h</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>
                    {isAdmin && (
                      <button className="btn secondary" onClick={() => remove(p.id)}>Delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function Deliveries() {
  // Recent outbox dispatches — emails, webhooks, channel sends. Lets admins
  // confirm broadcast / password-reset / outbound-message wiring without
  // tailing the worker logs.
  const [list, setList] = useState([]);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.get('/workspace/delivery-log')
      .then(setList)
      .catch((e) => setErr(String(e?.message || e)));
  }, []);
  return (
    <>
      <h3 style={{ marginBottom: 6, fontSize: 16 }}>Recent deliveries</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 13 }}>
        Outbox dispatches over the last little while — emails, webhooks, channel sends.
        Configure SMTP via <code>SMTP_HOST</code> to deliver mail; otherwise sends are
        logged here for inspection.
      </p>
      {err && <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13 }}>{err}</div>}
      {list.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>Nothing dispatched yet.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
              <th style={{ padding: '6px 4px' }}>Kind</th>
              <th style={{ padding: '6px 4px' }}>Target</th>
              <th style={{ padding: '6px 4px' }}>Subject</th>
              <th style={{ padding: '6px 4px' }}>Status</th>
              <th style={{ padding: '6px 4px' }}>At</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 4px' }}><code>{r.kind}</code></td>
                <td style={{ padding: '8px 4px' }}>{r.target}</td>
                <td style={{ padding: '8px 4px' }}>{r.subject || '—'}</td>
                <td style={{ padding: '8px 4px' }}>
                  <span style={{ color: r.status === 'sent' ? 'var(--success, #059669)' : 'var(--danger, #b91c1c)' }}>
                    {r.status}
                  </span>
                  {r.error && <span style={{ color: 'var(--muted)', marginLeft: 6, fontSize: 12 }}>· {r.error}</span>}
                </td>
                <td style={{ padding: '8px 4px', color: 'var(--muted)' }}>
                  {r.at ? new Date(r.at).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function Billing() {
  const [me, setMe] = useState(null);
  const [plan, setPlan] = useState(null);
  const [usage, setUsage] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const reload = () => {
    Promise.all([
      api.get('/billing/plan').catch((err) => { logError('billing-plan')(err); return null; }),
      api.get('/billing/usage').catch((err) => { logError('billing-usage')(err); return null; }),
      api.get('/billing/invoices').catch((err) => { logError('billing-invoices')(err); return []; }),
    ]).then(([p, u, inv]) => { setPlan(p); setUsage(u); setInvoices(inv || []); });
  };
  useEffect(() => {
    api.get('/me').then(setMe).catch(logError('billing-me'));
    reload();
  }, []);

  const isAdmin = me?.role === 'admin';

  const upgrade = async (target) => {
    if (!isAdmin) return;
    setBusy(true); setErr(null);
    try {
      const res = await api.post('/billing/checkout', { plan: target, returnUrl: window.location.href });
      if (res?.url && res.mode === 'stripe') {
        window.location.href = res.url;
        return;
      }
      reload();
    } catch (e) {
      setErr(String(e?.message || e));
    } finally { setBusy(false); }
  };

  const openPortal = async () => {
    if (!isAdmin) return;
    setBusy(true); setErr(null);
    try {
      const res = await api.post('/billing/portal', {});
      if (res?.url) window.location.href = res.url;
      else setErr(res?.note || 'Portal not available');
    } catch (e) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  };

  if (!plan || !usage) return <div style={{ color: 'var(--muted)' }}>Loading…</div>;
  const fmtMoney = (cents, cur='usd') => `$${(cents/100).toFixed(cents % 100 === 0 ? 0 : 2)}`;
  return (
    <>
      <h3 style={{ marginBottom: 14, fontSize: 16 }}>Billing</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16 }}>
        You are on the <strong style={{ color: 'var(--text)' }}>{plan.name}</strong> plan
        {plan.priceCents ? ` — ${fmtMoney(plan.priceCents)}/month` : ''}.
        {plan.renewsAt && <span style={{ marginLeft: 6 }}>Renews {new Date(plan.renewsAt).toLocaleDateString()}.</span>}
      </p>

      <div className="stat-row" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="stat">
          <div className="label">Tickets this month</div>
          <div className="value">{usage.ticketsThisMonth.toLocaleString()}
            {usage.ticketLimit && <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 13 }}> / {usage.ticketLimit.toLocaleString()}</span>}
          </div>
        </div>
        <div className="stat">
          <div className="label">Active agents</div>
          <div className="value">{usage.agentsActive}
            {usage.agentLimit && <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 13 }}> / {usage.agentLimit}</span>}
          </div>
        </div>
        <div className="stat">
          <div className="label">Next invoice</div>
          <div className="value">{usage.nextInvoice ? new Date(usage.nextInvoice).toLocaleDateString() : '—'}</div>
        </div>
      </div>

      {err && <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13, marginTop: 10 }}>{err}</div>}

      <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>Change plan</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {plan.available.map((p) => {
            const current = p.id === plan.plan;
            return (
              <button key={p.id} className={'btn' + (current ? '' : ' secondary')}
                      disabled={!isAdmin || busy || current}
                      onClick={() => upgrade(p.id)}>
                {current ? '✓ ' : ''}{p.name}
                {p.priceCents > 0 && <span style={{ opacity: 0.7, marginLeft: 6 }}>${(p.priceCents/100).toFixed(0)}</span>}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button className="btn secondary" disabled={!isAdmin || busy} onClick={openPortal}>
          Manage in Stripe portal
        </button>
      </div>

      <div style={{ marginTop: 24 }}>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Invoices</div>
        {invoices.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>No invoices yet.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '6px 4px' }}>Number</th>
                <th style={{ padding: '6px 4px' }}>Period</th>
                <th style={{ padding: '6px 4px' }}>Amount</th>
                <th style={{ padding: '6px 4px' }}>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px', fontFamily: 'monospace' }}>{inv.number || inv.id}</td>
                  <td style={{ padding: '8px 4px', color: 'var(--muted)' }}>
                    {inv.periodStart ? new Date(inv.periodStart).toLocaleDateString() : '—'} → {inv.periodEnd ? new Date(inv.periodEnd).toLocaleDateString() : '—'}
                  </td>
                  <td style={{ padding: '8px 4px' }}>{fmtMoney(inv.amountCents, inv.currency)}</td>
                  <td style={{ padding: '8px 4px' }}>
                    <span style={{ color: inv.status === 'paid' ? 'var(--success, #059669)' : 'var(--muted)' }}>{inv.status}</span>
                    {inv.demo && <span style={{ color: 'var(--muted)', marginLeft: 6, fontSize: 11 }}>(demo)</span>}
                  </td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>
                    {(inv.hostedUrl || inv.pdfUrl) && (
                      <a href={inv.hostedUrl || inv.pdfUrl} target="_blank" rel="noreferrer"
                         style={{ color: 'var(--accent, #1e40af)' }}>View</a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function Members() {
  // Admin-only workspace membership management. GET /workspace/members backs
  // the list; POST /workspace/members invites a new user (server returns a
  // reset token the admin can hand off until SMTP delivery is configured).
  const [me, setMe] = useState(null);
  const [list, setList] = useState([]);
  const [state, save] = useAsyncSave();
  const [lastInvite, setLastInvite] = useState(null);
  const isAdmin = me?.role === 'admin';

  const reload = () => {
    api.get('/workspace/members').then(setList).catch((err) => {
      logError('members')(err);
      setList([]);
    });
  };
  useEffect(() => {
    api.get('/me').then(setMe).catch(logError('members-me'));
    reload();
  }, []);

  function onInvite(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const payload = {
      email: (form.get('email') || '').toString().trim(),
      name: (form.get('name') || '').toString().trim(),
      role: (form.get('role') || 'agent').toString(),
    };
    if (!payload.email) return;
    save(async () => {
      const row = await api.post('/workspace/members', payload);
      setLastInvite(row);
      e.target.reset();
      reload();
    });
  }

  return (
    <>
      <h3 style={{ marginBottom: 6, fontSize: 16 }}>Members</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16, fontSize: 13 }}>
        Everyone who can sign in to this workspace. Invite a teammate — they&apos;ll get
        an email with a reset link (until SMTP is configured, the reset token is shown
        below so you can hand it off manually).
      </p>
      {!isAdmin ? (
        <p style={{ color: 'var(--muted)', fontSize: 13 }}>Only admins can invite members.</p>
      ) : (
        <form onSubmit={onInvite} style={{ display: 'grid',
            gridTemplateColumns: '1fr 1fr auto auto', gap: 8, alignItems: 'end' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Email</label><input name="email" type="email" placeholder="teammate@example.com" required />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Name</label><input name="name" placeholder="Optional" />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Role</label>
            <select name="role" defaultValue="agent">
              <option value="viewer">Viewer</option>
              <option value="agent">Agent</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <SaveButton state={state} label="Invite" />
        </form>
      )}

      {lastInvite?.resetToken && (
        <div style={{ marginTop: 12, padding: 12, background: 'var(--surface-2, #f6f7f9)',
                      borderRadius: 8, fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Invite sent to {lastInvite.email}</div>
          <div style={{ color: 'var(--muted)', marginBottom: 6 }}>
            Reset token (valid 7 days) — share with the invitee to set their password:
          </div>
          <code style={{ fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>
            {lastInvite.resetToken}
          </code>
        </div>
      )}

      <div style={{ marginTop: 20 }}>
        {list.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>No members yet.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
                <th style={{ padding: '6px 4px' }}>Name</th>
                <th style={{ padding: '6px 4px' }}>Email</th>
                <th style={{ padding: '6px 4px' }}>Role</th>
                <th style={{ padding: '6px 4px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {list.map((m) => (
                <tr key={m.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px' }}>{m.name}</td>
                  <td style={{ padding: '8px 4px', color: 'var(--muted)' }}>{m.email}</td>
                  <td style={{ padding: '8px 4px' }}>
                    <span style={{
                      padding: '2px 8px', borderRadius: 999, fontSize: 11,
                      background: m.role === 'admin' ? 'var(--accent-soft, #dbeafe)' : 'var(--surface-2, #f6f7f9)',
                      color: m.role === 'admin' ? 'var(--accent, #1e40af)' : 'var(--text)',
                    }}>{m.role}</span>
                  </td>
                  <td style={{ padding: '8px 4px' }}>
                    <span style={{ color: m.status === 'invited' ? 'var(--warning, #b45309)' : 'var(--success, #059669)' }}>
                      {m.status || 'active'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

function Broadcasts() {
  // Admin-only: POST /workspace/broadcast fans a notification out to every
  // workspace member. GET /workspace/broadcasts backs the "Recent" list so
  // admins can see what they've already sent without bouncing to the
  // notifications feed.
  const [me, setMe] = useState(null);
  const [list, setList] = useState([]);
  const [state, save] = useAsyncSave();
  const isAdmin = me?.role === 'admin';
  useEffect(() => {
    api.get('/me').then(setMe).catch(logError('broadcasts-me'));
    api.get('/workspace/broadcasts').then(setList).catch((err) => {
      logError('broadcasts')(err);
      setList([]);
    });
  }, []);
  function onSubmit(e) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const title = (form.get('title') || '').toString().trim();
    const body = (form.get('body') || '').toString();
    const link = (form.get('link') || '').toString() || null;
    if (!title) return;
    save(async () => {
      await api.post('/workspace/broadcast', { title, body, link });
      e.target.reset();
      const refreshed = await api.get('/workspace/broadcasts').catch(() => []);
      setList(refreshed);
    });
  }
  return (
    <>
      <h3 style={{ marginBottom: 6, fontSize: 16 }}>Broadcasts</h3>
      <p style={{ color: 'var(--muted)', marginBottom: 16, fontSize: 13 }}>
        Send a workspace-wide announcement. Everyone on the team (except you) gets a notification in their bell feed.
      </p>
      {!isAdmin ? (
        <p style={{ color: 'var(--muted)', fontSize: 13 }}>Only admins can send broadcasts.</p>
      ) : (
        <form onSubmit={onSubmit}>
          <div className="form-group"><label>Title</label><input name="title" placeholder="Release 2.14 is live" required /></div>
          <div className="form-group"><label>Body</label><textarea name="body" rows={4} placeholder="What do you want the team to know?" /></div>
          <div className="form-group"><label>Optional link</label><input name="link" placeholder="/tickets?id=123" /></div>
          <SaveButton state={state} label="Send broadcast" />
        </form>
      )}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>Recent broadcasts</div>
        {list.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>No broadcasts yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {list.map((b) => (
              <div key={b.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
                <div style={{ fontWeight: 600 }}>{b.title}</div>
                {b.body && <div style={{ color: 'var(--muted)', fontSize: 13, marginTop: 2 }}>{b.body}</div>}
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>
                  {b.author || '—'} · {b.createdAt ? new Date(b.createdAt).toLocaleString() : ''}
                </div>
              </div>
            ))}
          </div>
        )}
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
