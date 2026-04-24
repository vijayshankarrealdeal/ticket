import { Fragment, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '../../components/Icons.jsx';
import { Spinner } from '../../components/Spinner.jsx';
import { api as apiClient } from '../../api.js';

const METHODS = ['ALL', 'GET', 'POST', 'PUT', 'DELETE'];
const EDITABLE_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'];

export default function ApiLibrary() {
  const navigate = useNavigate();
  const [tab, setTab] = useState('apis');
  const [query, setQuery] = useState('');
  const [method, setMethod] = useState('ALL');
  const [integration, setIntegration] = useState('ALL');
  const [apis, setApis] = useState([]);
  const [integrations, setIntegrations] = useState([]);
  const [modal, setModal] = useState(null); // { mode: 'editApi'|'newIntegration', data? }
  const [testOutput, setTestOutput] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);

  async function refresh() {
    // Pull both the legacy `/apis` rows and the saved Playground endpoints so
    // the Library is the single canonical view of every API the workspace
    // can call. Playground rows are tagged so we route their row actions
    // back to the Playground instead of the legacy edit modal.
    const [legacyResp, integrationsResp, pgEndpointsResp, pgFoldersResp] = await Promise.all([
      apiClient.get('/apis').catch(() => []),
      apiClient.get('/integrations').catch(() => []),
      apiClient.get('/api-playground/endpoints').catch(() => []),
      apiClient.get('/api-playground/folders').catch(() => []),
    ]);
    const legacy = (Array.isArray(legacyResp) ? legacyResp : legacyResp?.items || [])
      .map((row) => ({ ...row, source: 'legacy' }));

    // Folder lookup so each playground row can carry its parent folder name —
    // shown as a breadcrumb prefix in the Name column for quick scanning.
    const folderName = new Map();
    (Array.isArray(pgFoldersResp) ? pgFoldersResp : []).forEach((f) => {
      folderName.set(f.id, f.name);
    });

    const playground = (Array.isArray(pgEndpointsResp) ? pgEndpointsResp : []).map((ep) => ({
      id: ep.id,
      name: ep.name,
      folderName: ep.folderId ? folderName.get(ep.folderId) || 'Unfiled' : 'Unfiled',
      method: ep.method,
      description: ep.description || '',
      integrationId: null,
      integration: 'Playground',
      spec: { url: ep.url },
      calls24h: 0,
      p95: 0,
      lastUsed: ep.lastRunAt || null,
      source: 'playground',
      hasSchema: !!ep.outputSchema,
    }));
    setApis([...playground, ...legacy]);
    setIntegrations(Array.isArray(integrationsResp) ? integrationsResp : integrationsResp?.items || []);
  }

  useEffect(() => {
    refresh().finally(() => setInitialLoading(false));
  }, []);

  const integrationById = useMemo(() => {
    const m = new Map();
    integrations.forEach((i) => m.set(i.id, i.name));
    return m;
  }, [integrations]);

  const enrichedApis = useMemo(
    () => apis.map((a) => ({
      ...a,
      integration: integrationById.get(a.integrationId) || a.integration || '—',
    })),
    [apis, integrationById],
  );

  const integrationOptions = useMemo(
    () => ['ALL', ...Array.from(new Set(enrichedApis.map((a) => a.integration).filter(Boolean)))],
    [enrichedApis],
  );

  const filteredApis = useMemo(() => {
    const q = query.trim().toLowerCase();
    return enrichedApis.filter((a) => {
      if (method !== 'ALL' && a.method !== method) return false;
      if (integration !== 'ALL' && a.integration !== integration) return false;
      if (!q) return true;
      return (
        (a.name || '').toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q) ||
        (a.integration || '').toLowerCase().includes(q) ||
        (a.folderName || '').toLowerCase().includes(q)
      );
    });
  }, [enrichedApis, query, method, integration]);

  const filteredIntegrations = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return integrations;
    return integrations.filter(
      (i) => (i.name || '').toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q),
    );
  }, [integrations, query]);

  const deleteApi = async (row) => {
    if (!confirm('Delete this API entry?')) return;
    const path = row.source === 'playground'
      ? `/api-playground/endpoints/${encodeURIComponent(row.id)}`
      : `/apis/${encodeURIComponent(row.id)}`;
    try { await apiClient.del(path); } catch {}
    setApis((prev) => prev.filter((a) => a.id !== row.id));
  };

  const cloneApi = async (id) => {
    try {
      const copy = await apiClient.post(`/apis/${encodeURIComponent(id)}/clone`);
      if (copy) {
        setApis((prev) => [copy, ...prev]);
      }
    } catch (e) {
      alert(`Clone failed: ${e.message || e}`);
    }
  };

  const testApi = async (a) => {
    setTestOutput({ id: a.id, loading: true });
    try {
      const res = await apiClient.post(`/apis/${encodeURIComponent(a.id)}/test`, {});
      setTestOutput({ id: a.id, loading: false, result: res });
    } catch (e) {
      setTestOutput({ id: a.id, loading: false, error: e.message || String(e) });
    }
  };

  const deleteIntegration = async (id) => {
    if (!confirm('Delete this integration?')) return;
    try { await apiClient.del(`/integrations/${encodeURIComponent(id)}`); } catch {}
    setIntegrations((prev) => prev.filter((i) => i.id !== id));
  };

  const downloadLogs = async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || '/api'}/api-logs/stream?limit=5000`, {
        headers: (() => {
          try {
            const raw = localStorage.getItem('workspacex.auth');
            const t = raw && (JSON.parse(raw).token || JSON.parse(raw).accessToken);
            return t ? { Authorization: `Bearer ${t}` } : {};
          } catch { return {}; }
        })(),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'api-logs.ndjson';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Download failed: ${e.message || e}`);
    }
  };

  return (
    <div className="ch-page">
      <div className="ch-page-head">
        <div>
          <div className="ch-breadcrumb">
            <span>Channels</span>
            <Icon.Chevron className="ch-breadcrumb-sep" />
            <span className="ch-breadcrumb-current">API Library</span>
          </div>
          <h1 className="ch-page-title">API Library</h1>
          <p className="ch-page-sub">
            Design, test and manage the APIs your automations and agents call at runtime.
          </p>
        </div>
        <div className="ch-page-head-actions">
          <button className="btn secondary" onClick={downloadLogs}>
            <Icon.Book /> Export logs
          </button>
          {tab === 'apis' ? (
            <button className="btn" onClick={() => navigate('/api-playground')}>
              <Icon.Bolt /> Open Playground
            </button>
          ) : (
            <button className="btn" onClick={() => setModal({ mode: 'newIntegration' })}>
              <Icon.Plus /> New integration
            </button>
          )}
        </div>
      </div>

      {initialLoading && apis.length === 0 && integrations.length === 0 ? (
        <div style={{ padding: '80px 16px', display: 'flex', justifyContent: 'center' }}>
          <Spinner size={32} />
        </div>
      ) : (
      <>
      <div className="ch-kpi-row">
        <Kpi label="Total APIs" value={apis.length} />
        <Kpi label="Integrations" value={integrations.length} />
        <Kpi
          label="Calls · 24h"
          value={apis.reduce((s, a) => s + (a.calls24h || 0), 0).toLocaleString()}
        />
        <Kpi
          label="p95 latency"
          value={apis.length ? Math.round(apis.reduce((s, a) => s + (a.p95 || 0), 0) / apis.length) + 'ms' : '—'}
          tone="success"
        />
      </div>

      <div className="ch-segment">
        <button
          className={'ch-segment-btn' + (tab === 'apis' ? ' active' : '')}
          onClick={() => setTab('apis')}
        >
          APIs
          <span className="ch-segment-count">{apis.length}</span>
        </button>
        <button
          className={'ch-segment-btn' + (tab === 'integrations' ? ' active' : '')}
          onClick={() => setTab('integrations')}
        >
          Integrations
          <span className="ch-segment-count">{integrations.length}</span>
        </button>
      </div>

      <div className="ch-toolbar">
        <div className="ch-search grow">
          <Icon.Search />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tab === 'apis' ? 'Search APIs by name, description or integration' : 'Search integrations'}
          />
          {query && (
            <button className="ch-search-clear" onClick={() => setQuery('')}>
              <Icon.Close />
            </button>
          )}
        </div>
        {tab === 'apis' && (
          <>
            <Dropdown
              label="Method"
              value={method}
              options={METHODS.map((m) => ({ value: m, label: m }))}
              onChange={setMethod}
            />
            <Dropdown
              label="Integration"
              value={integration}
              options={integrationOptions.map((i) => ({ value: i, label: i }))}
              onChange={setIntegration}
            />
          </>
        )}
      </div>

      {tab === 'apis' ? (
        <ApiTable
          items={filteredApis}
          onClone={cloneApi}
          onDelete={deleteApi}
          onEdit={(a) => setModal({ mode: 'editApi', data: a })}
          onTest={testApi}
          onOpenInPlayground={(a) => navigate('/api-playground')}
          testOutput={testOutput}
        />
      ) : (
        <IntegrationsTable items={filteredIntegrations} onDelete={deleteIntegration} />
      )}
      </>
      )}

      {modal && (
        <ApiModal
          mode={modal.mode}
          initial={modal.data}
          integrations={integrations}
          onClose={() => setModal(null)}
          onSaved={async () => { setModal(null); await refresh(); }}
        />
      )}
    </div>
  );
}

function Kpi({ label, value, delta, tone }) {
  return (
    <div className="ch-kpi">
      <div className="ch-kpi-label">{label}</div>
      <div className="ch-kpi-value">{value}</div>
      {delta && <div className={'ch-kpi-delta tone-' + tone}>{delta}</div>}
    </div>
  );
}

function Dropdown({ label, value, options, onChange }) {
  return (
    <label className="ch-dropdown">
      <span className="ch-dropdown-label">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon.Chevron className="ch-dropdown-caret" />
    </label>
  );
}

function MethodBadge({ method }) {
  return <span className={'ch-method ch-method-' + (method || '').toLowerCase()}>{method}</span>;
}

function ApiTable({ items, onClone, onDelete, onEdit, onTest, onOpenInPlayground, testOutput }) {
  if (items.length === 0) return <EmptyTable label="No APIs match your filters" />;
  return (
    <div className="ch-table-wrap">
      <table className="ch-table">
        <thead>
          <tr>
            <th style={{ width: 72 }}>Method</th>
            <th>Name</th>
            <th>Source</th>
            <th className="num">Calls · 24h</th>
            <th className="num">p95</th>
            <th>Last used</th>
            <th style={{ width: 280 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => {
            const isPlayground = a.source === 'playground';
            return (
            <Fragment key={a.id}>
              <tr>
                <td><MethodBadge method={a.method} /></td>
                <td>
                  <div className="ch-api-name">
                    {isPlayground && a.folderName && (
                      <>
                        <span style={{ color: 'var(--muted)', fontWeight: 500 }}>
                          {a.folderName}
                        </span>
                        <span style={{ color: 'var(--muted-2, var(--muted))', margin: '0 6px', fontWeight: 500 }}>/</span>
                      </>
                    )}
                    {a.name}
                    {isPlayground && a.hasSchema && (
                      <span className="ch-chip" style={{ marginLeft: 6, fontSize: 10 }}>schema</span>
                    )}
                  </div>
                  <div className="ch-api-desc">{a.description || (isPlayground ? <em style={{ color: 'var(--warning, #b45309)' }}>No description — add one in the Playground</em> : '')}</div>
                </td>
                <td>
                  <span className={'ch-chip' + (isPlayground ? ' ch-chip-info' : '')}>
                    {isPlayground ? 'Playground' : (a.integration || '—')}
                  </span>
                </td>
                <td className="num">{(a.calls24h || 0).toLocaleString()}</td>
                <td className="num">{a.p95 || 0}</td>
                <td className="muted">{a.lastUsed || '—'}</td>
                <td>
                  <div className="ch-row-actions">
                    {isPlayground ? (
                      <button className="ch-row-action" onClick={() => onOpenInPlayground(a)}>
                        <Icon.ExternalLink /> Open
                      </button>
                    ) : (
                      <>
                        <button className="ch-row-action" onClick={() => onEdit(a)}>
                          <Icon.Pencil /> Edit
                        </button>
                        <button className="ch-row-action" onClick={() => onClone(a.id)}>
                          <Icon.Copy /> Clone
                        </button>
                        <button className="ch-row-action" onClick={() => onTest(a)}>
                          <Icon.Play /> Test
                        </button>
                      </>
                    )}
                    <button className="ch-row-action danger" onClick={() => onDelete(a)}>
                      <Icon.Trash /> Delete
                    </button>
                  </div>
                </td>
              </tr>
              {testOutput && testOutput.id === a.id && (
                <tr>
                  <td colSpan={7} style={{ background: 'var(--bg-soft, #f8fafc)' }}>
                    {testOutput.loading ? (
                      <div style={{ padding: 12, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
                        Running test…
                      </div>
                    ) : testOutput.error ? (
                      <pre style={{ padding: 12, color: 'var(--danger)', fontSize: 12 }}>
                        {testOutput.error}
                      </pre>
                    ) : (
                      <pre style={{ padding: 12, fontSize: 12, whiteSpace: 'pre-wrap' }}>
                        {JSON.stringify(testOutput.result, null, 2)}
                      </pre>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function IntegrationsTable({ items, onDelete }) {
  if (items.length === 0) return <EmptyTable label="No integrations match your search" />;
  return (
    <div className="ch-table-wrap">
      <table className="ch-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Description</th>
            <th style={{ width: 140 }}>Health</th>
            <th style={{ width: 220 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id}>
              <td>
                <div className="ch-api-name">{i.name}</div>
              </td>
              <td className="muted">{i.description || '—'}</td>
              <td>
                <span
                  className={'ch-pill ch-pill-' + (i.health === 'good' ? 'success' : 'warning')}
                >
                  <span className="ch-pill-dot" />
                  {i.health === 'good' ? 'Operational' : 'Degraded'}
                </span>
              </td>
              <td>
                <div className="ch-row-actions">
                  <button className="ch-row-action danger" onClick={() => onDelete(i.id)}>
                    <Icon.Trash /> Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyTable({ label }) {
  return (
    <div className="ch-table-empty">
      <Icon.InboxEmpty />
      <div className="ch-empty-title">{label}</div>
      <div className="ch-empty-sub">Adjust your filters or create a new one.</div>
    </div>
  );
}

function ApiModal({ mode, initial, integrations, onClose, onSaved }) {
  const isNewIntegration = mode === 'newIntegration';
  const [form, setForm] = useState(() => {
    if (isNewIntegration) return { name: '', description: '', kind: 'custom' };
    if (initial) return {
      name: initial.name || '',
      method: initial.method || 'GET',
      description: initial.description || '',
      integrationId: initial.integrationId || '',
      url: initial.spec?.url || '',
    };
    return { name: '', method: 'GET', description: '', integrationId: '', url: '' };
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  const setField = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      if (isNewIntegration) {
        await apiClient.post('/integrations', {
          name: form.name, description: form.description, kind: form.kind || 'custom',
        });
      } else {
        const body = {
          name: form.name, method: form.method, description: form.description,
          integrationId: form.integrationId || null,
          spec: { url: form.url || null },
        };
        if (initial?.id) {
          await apiClient.patch(`/apis/${encodeURIComponent(initial.id)}`, body);
        } else {
          await apiClient.post('/apis', body);
        }
      }
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="ch-modal-overlay" onClick={onClose}>
      <div className="ch-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="ch-modal-title">
          {isNewIntegration ? 'New integration' : initial?.id ? 'Edit API' : 'Add API'}
        </h3>
        <div className="ch-modal-body">
          <label className="ch-modal-field">Name
            <input value={form.name} onChange={(e) => setField('name', e.target.value)} />
          </label>
          {isNewIntegration ? (
            <label className="ch-modal-field">Description
              <input value={form.description} onChange={(e) => setField('description', e.target.value)} />
            </label>
          ) : (
            <>
              <label className="ch-modal-field">Method
                <select value={form.method} onChange={(e) => setField('method', e.target.value)}>
                  {EDITABLE_METHODS.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
              <label className="ch-modal-field">Description
                <input value={form.description} onChange={(e) => setField('description', e.target.value)} />
              </label>
              <label className="ch-modal-field">Integration
                <select
                  value={form.integrationId}
                  onChange={(e) => setField('integrationId', e.target.value)}
                >
                  <option value="">—</option>
                  {integrations.map((i) => (
                    <option key={i.id} value={i.id}>{i.name}</option>
                  ))}
                </select>
              </label>
              <label className="ch-modal-field">URL (use {'{{arg}}'} tokens)
                <input
                  value={form.url}
                  onChange={(e) => setField('url', e.target.value)}
                  placeholder="https://api.example.com/{{id}}"
                />
              </label>
            </>
          )}
        </div>
        {err && <div className="ch-modal-error">{err}</div>}
        <div className="ch-modal-foot">
          <button className="btn secondary" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={save} disabled={saving || !form.name}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
