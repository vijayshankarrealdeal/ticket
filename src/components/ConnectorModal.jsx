import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon } from './Icons.jsx';

function fmtRelative(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return `${Math.floor(diff / 86400)} d ago`;
}

function fmtAbsolute(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Connect / configure an integration channel.
 *
 * Fetches the credential schema from `/platforms/:id/schema`, renders a form
 * for every field the provider wants, POSTs the values back to
 * `/platforms/:id/configure`, and surfaces the result of a best-effort
 * liveness probe so the admin knows the creds actually work before closing.
 *
 * For `live-chat` we skip credential entry and render the embed snippet —
 * the widget endpoint needs nothing more than the workspace slug.
 */
export default function ConnectorModal({ platform, onClose, onSaved }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [copied, setCopied] = useState(false);
  const firstInput = useRef(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    api.get(`/platforms/${encodeURIComponent(platform.id)}/schema`)
      .then((res) => {
        if (!alive) return;
        setSchema(res);
        // Seed form values from defaults + any already-saved meta values so
        // reopening the modal shows what's there today rather than blanks.
        const seeded = {};
        (res?.fields || []).forEach((f) => {
          if (f.default !== undefined && f.default !== null) seeded[f.key] = f.default;
        });
        const meta = res?.existing?.meta || {};
        Object.entries(meta).forEach(([k, v]) => {
          if (v !== undefined && v !== null && v !== '') seeded[k] = v;
        });
        setValues(seeded);
      })
      .catch((e) => { if (alive) setError(e?.message || String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [platform.id]);

  useEffect(() => {
    if (firstInput.current) firstInput.current.focus();
  }, [schema]);

  const isLiveChat = platform.id === 'live-chat';
  const isOAuth = schema?.kind === 'oauth';
  const hasStored = Boolean(schema?.existing) || (isOAuth && schema?.connected);

  const setField = (k, v) => setValues((prev) => ({ ...prev, [k]: v }));

  const save = async (e) => {
    e?.preventDefault?.();
    setSaving(true); setError(null); setTestResult(null);
    try {
      await api.post(`/platforms/${encodeURIComponent(platform.id)}/configure`, {
        credentials: values,
      });
      // Fire a liveness probe so the admin can see immediately whether the
      // credentials actually work. Non-fatal — save succeeded regardless.
      try {
        setTesting(true);
        const r = await api.post(`/platforms/${encodeURIComponent(platform.id)}/test`);
        setTestResult(r);
      } catch (err) {
        setTestResult({ ok: false, message: err?.message || String(err) });
      } finally {
        setTesting(false);
      }
      onSaved?.();
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true); setTestResult(null);
    try {
      const r = await api.post(`/platforms/${encodeURIComponent(platform.id)}/test`);
      setTestResult(r);
    } catch (err) {
      setTestResult({ ok: false, message: err?.message || String(err) });
    } finally {
      setTesting(false);
    }
  };

  const runSync = async () => {
    setTesting(true); setTestResult(null);
    try {
      const r = await api.post(`/platforms/${encodeURIComponent(platform.id)}/sync`);
      const accounts = r?.accounts ?? 0;
      const ingested = r?.ingested ?? 0;
      const fetched = r?.fetched ?? 0;
      setTestResult({
        ok: !r?.errors?.length,
        message: `Pulled ${ingested} new / ${fetched} unread across ${accounts} account(s).`,
        detail: r?.errors?.length
          ? r.errors.map((e) => `${e.account || ''}: ${e.error}`).join('\n')
          : null,
      });
    } catch (err) {
      setTestResult({ ok: false, message: err?.message || String(err) });
    } finally {
      setTesting(false);
    }
  };

  const reconnectOAuth = async () => {
    if (!isOAuth) return;
    const base = import.meta.env.VITE_API_URL || '/api';
    const { getHandoffToken } = await import('../api.js');
    const token = (await getHandoffToken()) || '';
    const returnTo = window.location.origin + window.location.pathname;
    const params = { mode: 'connect', return_to: returnTo };
    if (token) params.token = token;
    const qs = new URLSearchParams(params).toString();
    window.location.href = `${base}/auth/oauth/${schema.oauth_provider}/start?${qs}`;
  };

  const embedSnippet = useMemo(() => {
    if (!isLiveChat || !schema?.embed_url) return null;
    const delay = values.proactive_ms ? ` data-proactive-ms="${values.proactive_ms}"` : '';
    return `<script src="${schema.embed_url}" async${delay}></script>`;
  }, [isLiveChat, schema?.embed_url, values.proactive_ms]);

  const copy = async () => {
    if (!embedSnippet) return;
    try {
      await navigator.clipboard.writeText(embedSnippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard blocked — fall through */ }
  };

  const masked = schema?.existing?.secrets_masked || {};

  return (
    <div className="ch-modal-overlay" onClick={onClose}>
      <div
        className="ch-modal"
        style={{ width: 560 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <div
            className="ch-card-logo"
            style={{ background: platform.accent, width: 36, height: 36, fontSize: 16 }}
          >
            {platform.letter || platform.name?.[0]}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 className="ch-modal-title" style={{ margin: 0 }}>
              {hasStored ? 'Manage ' : 'Connect '} {platform.name}
            </h3>
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>
              {platform.category}
            </div>
          </div>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">
            <Icon.Close />
          </button>
        </div>

        {schema?.overview && (
          <p style={{ fontSize: 13, color: 'var(--text-2)', margin: '6px 0 14px' }}>
            {schema.overview}{' '}
            {schema.docs_url && (
              <a href={schema.docs_url} target="_blank" rel="noreferrer"
                 style={{ color: 'var(--primary)' }}>
                Open provider docs ↗
              </a>
            )}
          </p>
        )}

        {loading && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
            Loading…
          </div>
        )}

        {!loading && schema && isOAuth && (
          <div className="ch-modal-body">
            <OAuthDetail schema={schema} platform={platform} />

            {testResult && (
              <div style={{
                marginTop: 4, padding: '8px 10px', borderRadius: 8, fontSize: 12,
                background: testResult.ok
                  ? 'color-mix(in srgb, var(--success, #10b981) 12%, transparent)'
                  : 'color-mix(in srgb, var(--danger, #ef4444) 12%, transparent)',
                color: testResult.ok ? 'var(--success, #10b981)' : 'var(--danger, #ef4444)',
                border: `1px solid ${testResult.ok ? 'var(--success, #10b981)' : 'var(--danger, #ef4444)'}`,
                whiteSpace: 'pre-line',
              }}>
                {testResult.ok ? '✓ ' : '✗ '}
                {testResult.message || (testResult.ok ? 'Done.' : 'Failed.')}
                {testResult.detail && (
                  <div style={{ marginTop: 4, opacity: 0.8 }}>{String(testResult.detail).slice(0, 400)}</div>
                )}
              </div>
            )}

            <div className="ch-modal-foot">
              {schema.connected && schema.supports_sync && (
                <button
                  type="button"
                  className="btn ghost sm"
                  disabled={testing}
                  onClick={runSync}
                >
                  {testing ? 'Syncing…' : 'Sync inbox now'}
                </button>
              )}
              <button type="button" className="btn secondary sm" onClick={onClose}>
                Close
              </button>
              <button type="button" className="btn sm" onClick={reconnectOAuth}>
                {schema.connected ? 'Reconnect' : 'Connect with ' + (schema.oauth_provider || 'OAuth')}
              </button>
            </div>
          </div>
        )}

        {!loading && schema && !isOAuth && (
          <form className="ch-modal-body" onSubmit={save} autoComplete="off">
            {isLiveChat ? (
              <LiveChatEmbed
                schema={schema}
                values={values}
                setField={setField}
                snippet={embedSnippet}
                onCopy={copy}
                copied={copied}
              />
            ) : (
              (schema.fields || []).map((f, idx) => (
                <Field
                  key={f.key}
                  field={f}
                  value={values[f.key] ?? ''}
                  placeholder={f.placeholder
                    || (masked[f.key] ? `Stored: ${masked[f.key]}` : '')}
                  onChange={(v) => setField(f.key, v)}
                  inputRef={idx === 0 ? firstInput : null}
                />
              ))
            )}

            {schema.webhook_instructions && (
              <div style={{
                marginTop: 4, padding: '10px 12px', borderRadius: 8,
                background: 'var(--panel-2)', border: '1px solid var(--border)',
                fontSize: 12, color: 'var(--text-2)',
              }}>
                <b style={{ color: 'var(--text)' }}>Webhook setup</b>
                <div style={{ marginTop: 4 }}>{schema.webhook_instructions}</div>
              </div>
            )}

            {error && <div className="ch-modal-error">{error}</div>}

            {testResult && (
              <div style={{
                marginTop: 4, padding: '8px 10px', borderRadius: 8, fontSize: 12,
                background: testResult.ok
                  ? 'color-mix(in srgb, var(--success, #10b981) 12%, transparent)'
                  : 'color-mix(in srgb, var(--danger, #ef4444) 12%, transparent)',
                color: testResult.ok ? 'var(--success, #10b981)' : 'var(--danger, #ef4444)',
                border: `1px solid ${testResult.ok ? 'var(--success, #10b981)' : 'var(--danger, #ef4444)'}`,
              }}>
                {testResult.ok ? '✓ ' : '✗ '}
                {testResult.message || (testResult.ok ? 'Connected.' : 'Probe failed.')}
                {testResult.detail && (
                  <div style={{ marginTop: 4, opacity: 0.8 }}>{String(testResult.detail).slice(0, 300)}</div>
                )}
              </div>
            )}

            <div className="ch-modal-foot">
              {hasStored && (
                <button
                  type="button"
                  className="btn ghost sm"
                  disabled={testing}
                  onClick={runTest}
                >
                  {testing ? 'Testing…' : 'Test connection'}
                </button>
              )}
              <button type="button" className="btn secondary sm" onClick={onClose}>
                Close
              </button>
              {!isLiveChat && (
                <button type="submit" className="btn sm" disabled={saving}>
                  {saving ? 'Saving…' : hasStored ? 'Update credentials' : 'Connect'}
                </button>
              )}
              {isLiveChat && (
                <button type="submit" className="btn sm" disabled={saving}>
                  {saving ? 'Saving…' : hasStored ? 'Save changes' : 'Enable widget'}
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function Field({ field, value, placeholder, onChange, inputRef }) {
  const type = field.type || (field.secret ? 'password' : 'text');
  if (type === 'boolean') {
    return (
      <label className="ch-modal-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          style={{ width: 'auto', height: 'auto' }}
        />
        <span>{field.label}{field.required && <span style={{ color: 'var(--danger)' }}> *</span>}</span>
        {field.help && <span style={{ color: 'var(--muted)', fontSize: 12 }}>— {field.help}</span>}
      </label>
    );
  }
  return (
    <label className="ch-modal-field">
      <span>
        {field.label}
        {field.required && <span style={{ color: 'var(--danger)' }}> *</span>}
      </span>
      <input
        ref={inputRef || undefined}
        type={type === 'number' ? 'number' : type}
        autoComplete={field.secret ? 'new-password' : 'off'}
        spellCheck={false}
        placeholder={placeholder || ''}
        value={value ?? ''}
        onChange={(e) => onChange(type === 'number' ? Number(e.target.value || 0) : e.target.value)}
      />
      {field.help && (
        <span style={{ color: 'var(--muted)', fontSize: 11 }}>{field.help}</span>
      )}
    </label>
  );
}

function OAuthDetail({ schema, platform }) {
  if (!schema?.connected) {
    return (
      <div style={{
        padding: '14px 16px', borderRadius: 10,
        background: 'var(--panel-2)', border: '1px solid var(--border)',
        fontSize: 13, color: 'var(--text-2)',
      }}>
        <div style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4 }}>
          Not connected
        </div>
        Sign in with {schema.oauth_provider} to grant {platform.name} access
        to {schema.scopes_summary || 'your mailbox'}.
      </div>
    );
  }

  const rows = [
    { label: 'Linked email', value: schema.linked_email },
    schema.linked_name ? { label: 'Account name', value: schema.linked_name } : null,
    {
      label: 'Connected',
      value: schema.connected_at
        ? `${fmtRelative(schema.connected_at)} · ${fmtAbsolute(schema.connected_at)}`
        : '—',
    },
    schema.expires_at
      ? {
          label: 'Token refreshes',
          value: `${fmtRelative(schema.expires_at) || ''} · ${fmtAbsolute(schema.expires_at) || ''}`,
        }
      : null,
    schema.installed_by_email
      ? { label: 'Installed by', value: schema.installed_by_email }
      : null,
    schema.scopes_summary ? { label: 'Granted scopes', value: schema.scopes_summary } : null,
  ].filter(Boolean);

  return (
    <>
      <div style={{
        display: 'grid', gridTemplateColumns: '120px 1fr', gap: '8px 14px',
        padding: '14px 16px', borderRadius: 10,
        background: 'var(--panel-2)', border: '1px solid var(--border)',
        fontSize: 13,
      }}>
        {rows.map((r) => (
          <RowKV key={r.label} label={r.label} value={r.value} />
        ))}
      </div>
      {schema.needs_reauth && (
        <div style={{
          padding: '8px 10px', borderRadius: 8, fontSize: 12,
          background: 'color-mix(in srgb, var(--warn, #f59e0b) 12%, transparent)',
          color: 'var(--warn, #f59e0b)',
          border: '1px solid var(--warn, #f59e0b)',
        }}>
          The stored token has expired or was revoked. Reconnect to resume sync.
        </div>
      )}
      {schema.docs_url && (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>
          <a href={schema.docs_url} target="_blank" rel="noreferrer"
             style={{ color: 'var(--primary)' }}>
            View granted scope reference ↗
          </a>
        </div>
      )}
    </>
  );
}

function RowKV({ label, value }) {
  return (
    <>
      <div style={{ color: 'var(--muted)' }}>{label}</div>
      <div style={{ color: 'var(--text)', wordBreak: 'break-word' }}>{value || '—'}</div>
    </>
  );
}

function LiveChatEmbed({ schema, values, setField, snippet, onCopy, copied }) {
  return (
    <>
      {(schema.fields || []).map((f) => (
        <Field
          key={f.key}
          field={f}
          value={values[f.key] ?? ''}
          placeholder={f.placeholder || ''}
          onChange={(v) => setField(f.key, v)}
        />
      ))}
      {snippet && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 4 }}>
            Paste this into your site's <code>&lt;head&gt;</code>:
          </div>
          <pre
            style={{
              margin: 0, padding: 10, borderRadius: 8,
              background: 'var(--panel-2)', border: '1px solid var(--border)',
              fontSize: 12, color: 'var(--text)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            }}
          >
            {snippet}
          </pre>
          <button
            type="button"
            className="btn ghost sm"
            onClick={onCopy}
            style={{ marginTop: 8 }}
          >
            {copied ? 'Copied!' : 'Copy snippet'}
          </button>
        </div>
      )}
    </>
  );
}
