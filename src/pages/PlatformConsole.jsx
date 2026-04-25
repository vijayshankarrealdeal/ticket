import { useEffect, useMemo, useState, useCallback } from 'react';

const BASE_URL = import.meta.env.VITE_API_URL || '/api';
// Separate storage key from the regular user auth so the two sessions don't
// stomp on each other in the same browser tab.
const PLATFORM_AUTH_KEY = 'workspacex.platform';

function loadPlatformAuth() {
  try { return JSON.parse(localStorage.getItem(PLATFORM_AUTH_KEY) || 'null'); } catch { return null; }
}
function savePlatformAuth(v) {
  try { localStorage.setItem(PLATFORM_AUTH_KEY, JSON.stringify(v)); } catch {}
}
function clearPlatformAuth() {
  try { localStorage.removeItem(PLATFORM_AUTH_KEY); } catch {}
}

async function pApi(method, path, body) {
  const headers = { Accept: 'application/json' };
  const auth = loadPlatformAuth();
  const token = auth?.token;
  if (token) headers.Authorization = `Bearer ${token}`;
  const opts = { method, headers };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${BASE_URL}${path}`, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`${res.status}: ${text}`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export default function PlatformConsole() {
  const [auth, setAuth] = useState(() => loadPlatformAuth());
  const [booting, setBooting] = useState(!!auth);

  // On mount, if we have a cached token, verify it's still valid — otherwise
  // drop the stale auth and show the sign-in form.
  useEffect(() => {
    if (!auth) return;
    let cancelled = false;
    pApi('GET', '/platform/auth/me')
      .then(() => { if (!cancelled) setBooting(false); })
      .catch(() => {
        if (cancelled) return;
        clearPlatformAuth();
        setAuth(null);
        setBooting(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (booting) {
    return <div style={{ padding: 40, color: 'var(--muted)' }}>Loading platform console…</div>;
  }

  if (!auth) {
    return <PlatformLogin onAuth={(next) => { savePlatformAuth(next); setAuth(next); }} />;
  }

  return (
    <PlatformDashboard
      admin={auth.admin}
      onSignOut={async () => {
        try { await pApi('POST', '/platform/auth/signout'); } catch {}
        clearPlatformAuth();
        setAuth(null);
      }}
    />
  );
}

function PlatformLogin({ onAuth }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await pApi('POST', '/platform/auth/signin', { email, password });
      onAuth({ admin: res.admin, token: res.token });
    } catch (err) {
      setError(err.status === 401 ? 'Invalid super-user credentials.' : 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'grid', placeItems: 'center',
      background: 'var(--bg, #0b0d12)',
    }}>
      <form onSubmit={submit} style={{
        width: 360, padding: 28,
        background: 'var(--panel, #fff)',
        border: '1px solid var(--border)',
        borderRadius: 16,
        boxShadow: '0 8px 30px rgba(0,0,0,0.08)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div>
          <div style={{ fontSize: 12, letterSpacing: 3, color: 'var(--muted)', textTransform: 'uppercase' }}>
            WorkspaceX
          </div>
          <h1 style={{ margin: '4px 0 0', fontSize: 20, fontWeight: 700 }}>Platform Console</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--muted)', fontSize: 13 }}>
            Super-users only. Create organizations and manage members.
          </p>
        </div>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span style={{ color: 'var(--muted)' }}>Email</span>
          <input
            type="email" autoComplete="username" required
            value={email} onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span style={{ color: 'var(--muted)' }}>Password</span>
          <input
            type="password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
        </label>
        {error && <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13 }}>{error}</div>}
        <button type="submit" disabled={busy} className="btn" style={{ marginTop: 6 }}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

const inputStyle = {
  height: 40, padding: '0 12px',
  border: '1.5px solid var(--border)', borderRadius: 10,
  background: 'var(--panel)', color: 'var(--text)',
  fontSize: 14,
};

function PlatformDashboard({ admin, onSignOut }) {
  const [orgs, setOrgs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [newOrgName, setNewOrgName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newRole, setNewRole] = useState('agent');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const selected = useMemo(
    () => orgs.find((o) => o.id === selectedId) || null,
    [orgs, selectedId],
  );

  const refreshOrgs = useCallback(async () => {
    try {
      const list = await pApi('GET', '/platform/orgs');
      setOrgs(list);
      if (!selectedId && list.length) setSelectedId(list[0].id);
    } catch (err) {
      setError('Could not load organizations.');
    }
  }, [selectedId]);

  const refreshDetail = useCallback(async (id) => {
    if (!id) { setDetail(null); return; }
    try {
      const res = await pApi('GET', `/platform/orgs/${id}/members`);
      setDetail(res);
    } catch {
      setDetail(null);
    }
  }, []);

  useEffect(() => { refreshOrgs(); }, [refreshOrgs]);
  useEffect(() => { refreshDetail(selectedId); }, [selectedId, refreshDetail]);

  async function createOrg(e) {
    e.preventDefault();
    if (!newOrgName.trim()) return;
    setBusy(true); setError(null);
    try {
      const created = await pApi('POST', '/platform/orgs', { name: newOrgName.trim() });
      setNewOrgName('');
      await refreshOrgs();
      setSelectedId(created.id);
    } catch (err) {
      setError('Failed to create organization.');
    } finally {
      setBusy(false);
    }
  }

  async function deleteOrg(id) {
    const org = orgs.find((o) => o.id === id);
    if (!org) return;
    const confirmed = window.prompt(
      `Deleting "${org.name}" will remove its users, tickets, channels, and messages permanently. Type the organization name to confirm.`,
    );
    if (confirmed !== org.name) return;
    try {
      await pApi('DELETE', `/platform/orgs/${id}`);
      if (selectedId === id) setSelectedId(null);
      await refreshOrgs();
    } catch (err) {
      setError('Could not delete organization.');
    }
  }

  async function addEmail(e) {
    e.preventDefault();
    if (!selected || !newEmail.trim()) return;
    setBusy(true); setError(null);
    try {
      await pApi('POST', `/platform/orgs/${selected.id}/allowed-emails`, {
        email: newEmail.trim(), role: newRole,
      });
      setNewEmail('');
      await Promise.all([refreshDetail(selected.id), refreshOrgs()]);
    } catch (err) {
      setError(err.status === 409
        ? 'That email is already approved for an organization.'
        : 'Could not add email.');
    } finally {
      setBusy(false);
    }
  }

  async function removeEmail(email) {
    if (!selected) return;
    if (!window.confirm(`Revoke ${email}? They will no longer be able to sign up here.`)) return;
    try {
      await pApi('DELETE', `/platform/orgs/${selected.id}/allowed-emails/${encodeURIComponent(email)}`);
      await Promise.all([refreshDetail(selected.id), refreshOrgs()]);
    } catch {
      setError('Could not revoke email.');
    }
  }

  async function removeMember(user) {
    if (!selected) return;
    if (!window.confirm(`Remove ${user.email} from "${selected.name}"? Their allowlist entry is also revoked.`)) return;
    try {
      await pApi('DELETE', `/platform/orgs/${selected.id}/members/${user.id}`);
      await Promise.all([refreshDetail(selected.id), refreshOrgs()]);
    } catch {
      setError('Could not remove member.');
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', color: 'var(--text)' }}>
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 24px', borderBottom: '1px solid var(--border)',
        background: 'var(--panel)',
      }}>
        <div>
          <div style={{ fontSize: 11, letterSpacing: 3, color: 'var(--muted)', textTransform: 'uppercase' }}>
            WorkspaceX · Platform Console
          </div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>Super-user dashboard</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>{admin?.email}</span>
          <button className="btn" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      {error && (
        <div style={{
          padding: '10px 24px', background: 'var(--danger-50, #fee2e2)',
          color: 'var(--danger, #b91c1c)', fontSize: 13,
        }}>{error}</div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 24, padding: 24 }}>
        {/* Orgs sidebar */}
        <aside style={{
          border: '1px solid var(--border)', borderRadius: 14,
          background: 'var(--panel)', padding: 16,
          display: 'flex', flexDirection: 'column', gap: 14,
          height: 'fit-content',
        }}>
          <form onSubmit={createOrg} style={{ display: 'flex', gap: 8 }}>
            <input
              placeholder="New organization"
              value={newOrgName}
              onChange={(e) => setNewOrgName(e.target.value)}
              style={{ ...inputStyle, flex: 1 }}
            />
            <button type="submit" className="btn" disabled={busy || !newOrgName.trim()}>
              Create
            </button>
          </form>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {orgs.length === 0 && (
              <div style={{ color: 'var(--muted)', fontSize: 13, padding: 8 }}>
                No organizations yet.
              </div>
            )}
            {orgs.map((o) => (
              <button
                key={o.id}
                onClick={() => setSelectedId(o.id)}
                style={{
                  textAlign: 'left', padding: '10px 12px', borderRadius: 10,
                  border: '1px solid ' + (o.id === selectedId ? 'var(--accent, #1e40af)' : 'transparent'),
                  background: o.id === selectedId ? 'var(--hover)' : 'transparent',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 600 }}>{o.name}</div>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                  {o.member_count} member{o.member_count === 1 ? '' : 's'} ·{' '}
                  {o.allowed_count} allowed
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* Org detail */}
        <section style={{
          border: '1px solid var(--border)', borderRadius: 14,
          background: 'var(--panel)', padding: 20,
        }}>
          {!selected && (
            <div style={{ color: 'var(--muted)' }}>Select an organization on the left, or create one.</div>
          )}
          {selected && (
            <>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: 18 }}>{selected.name}</h2>
                  <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>
                    <code>{selected.id}</code>
                  </div>
                </div>
                <button
                  className="btn"
                  onClick={() => deleteOrg(selected.id)}
                  style={{ background: 'transparent', color: 'var(--danger, #b91c1c)', border: '1px solid var(--border)' }}
                >
                  Delete organization
                </button>
              </div>

              <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '18px 0' }} />

              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Approved emails</h3>
              <p style={{ color: 'var(--muted)', fontSize: 13, margin: '4px 0 12px' }}>
                Emails on this list can sign up or sign in (password or SSO) and will land in this organization.
              </p>
              <form onSubmit={addEmail} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                <input
                  type="email" placeholder="name@company.com"
                  value={newEmail} onChange={(e) => setNewEmail(e.target.value)}
                  style={{ ...inputStyle, flex: '1 1 240px' }}
                />
                <select value={newRole} onChange={(e) => setNewRole(e.target.value)} style={inputStyle}>
                  <option value="viewer">Viewer</option>
                  <option value="agent">Agent</option>
                  <option value="admin">Admin</option>
                </select>
                <button type="submit" className="btn" disabled={busy || !newEmail.trim()}>
                  Approve email
                </button>
              </form>
              <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
                {(!detail || detail.allowed.length === 0) && (
                  <div style={{ padding: 14, color: 'var(--muted)', fontSize: 13 }}>
                    No approved emails yet.
                  </div>
                )}
                {detail?.allowed.map((a) => (
                  <div key={a.email} style={{
                    display: 'grid', gridTemplateColumns: '1fr 100px 110px 120px',
                    alignItems: 'center', gap: 12,
                    padding: '10px 14px', borderBottom: '1px solid var(--border)',
                  }}>
                    <div style={{ fontWeight: 500 }}>{a.email}</div>
                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>{a.role}</div>
                    <div style={{ fontSize: 12 }}>
                      <span style={{
                        padding: '2px 8px', borderRadius: 999,
                        background: a.activated ? 'var(--success-50, #d1fae5)' : 'var(--hover)',
                        color: a.activated ? 'var(--success, #059669)' : 'var(--muted)',
                      }}>
                        {a.activated ? 'Activated' : 'Pending'}
                      </span>
                    </div>
                    <button
                      onClick={() => removeEmail(a.email)}
                      style={{
                        justifySelf: 'end', padding: '4px 10px', borderRadius: 8,
                        border: '1px solid var(--border)', background: 'transparent',
                        color: 'var(--danger, #b91c1c)', fontSize: 12, cursor: 'pointer',
                      }}
                    >
                      Revoke
                    </button>
                  </div>
                ))}
              </div>

              <h3 style={{ margin: '24px 0 8px', fontSize: 14, fontWeight: 700 }}>Active members</h3>
              <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
                {(!detail || detail.users.length === 0) && (
                  <div style={{ padding: 14, color: 'var(--muted)', fontSize: 13 }}>
                    No one has signed up yet.
                  </div>
                )}
                {detail?.users.map((u) => (
                  <div key={u.id} style={{
                    display: 'grid', gridTemplateColumns: '1fr 1fr 100px 120px',
                    alignItems: 'center', gap: 12,
                    padding: '10px 14px', borderBottom: '1px solid var(--border)',
                  }}>
                    <div style={{ fontWeight: 500 }}>{u.name}</div>
                    <div style={{ fontSize: 13, color: 'var(--muted)' }}>{u.email}</div>
                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>{u.role}</div>
                    <button
                      onClick={() => removeMember(u)}
                      style={{
                        justifySelf: 'end', padding: '4px 10px', borderRadius: 8,
                        border: '1px solid var(--border)', background: 'transparent',
                        color: 'var(--danger, #b91c1c)', fontSize: 12, cursor: 'pointer',
                      }}
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
