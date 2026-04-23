import { useEffect, useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { api as apiClient } from '../../api.js';

const METHODS = ['ALL', 'GET', 'POST', 'PUT', 'DELETE'];

export default function ApiLibrary() {
  const [tab, setTab] = useState('apis');
  const [query, setQuery] = useState('');
  const [method, setMethod] = useState('ALL');
  const [integration, setIntegration] = useState('ALL');
  const [apis, setApis] = useState([]);
  const [integrations, setIntegrations] = useState([]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      apiClient.get('/apis').catch(() => []),
      apiClient.get('/integrations').catch(() => []),
    ]).then(([a, i]) => {
      if (!alive) return;
      setApis(Array.isArray(a) ? a : a?.items || []);
      setIntegrations(Array.isArray(i) ? i : i?.items || []);
    });
    return () => { alive = false; };
  }, []);

  const integrationOptions = useMemo(
    () => ['ALL', ...Array.from(new Set(apis.map((a) => a.integration)))],
    [apis],
  );

  const filteredApis = useMemo(() => {
    const q = query.trim().toLowerCase();
    return apis.filter((a) => {
      if (method !== 'ALL' && a.method !== method) return false;
      if (integration !== 'ALL' && a.integration !== integration) return false;
      if (!q) return true;
      return (
        (a.name || '').toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q) ||
        (a.integration || '').toLowerCase().includes(q)
      );
    });
  }, [apis, query, method, integration]);

  const filteredIntegrations = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return integrations;
    return integrations.filter(
      (i) => (i.name || '').toLowerCase().includes(q) || (i.desc || '').toLowerCase().includes(q),
    );
  }, [integrations, query]);

  const deleteApi = async (id) => {
    try { await apiClient.del(`/apis/${encodeURIComponent(id)}`); } catch {}
    setApis((prev) => prev.filter((a) => a.id !== id));
  };
  const cloneApi = async (id) => {
    try {
      const copy = await apiClient.post(`/apis/${encodeURIComponent(id)}/clone`);
      if (copy) {
        setApis((prev) => [copy, ...prev]);
        return;
      }
    } catch {}
    setApis((prev) => {
      const base = prev.find((a) => a.id === id);
      if (!base) return prev;
      const copy = { ...base, id: Date.now(), name: base.name + '_copy', calls24h: 0, lastUsed: '—' };
      return [copy, ...prev];
    });
  };
  const deleteIntegration = async (id) => {
    try { await apiClient.del(`/integrations/${encodeURIComponent(id)}`); } catch {}
    setIntegrations((prev) => prev.filter((i) => i.id !== id));
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
          <button className="btn secondary"><Icon.Book /> Docs</button>
          {tab === 'apis' ? (
            <button className="btn"><Icon.Plus /> Add API</button>
          ) : (
            <button className="btn"><Icon.Plus /> New integration</button>
          )}
        </div>
      </div>

      <div className="ch-kpi-row">
        <Kpi label="Total APIs" value={apis.length} />
        <Kpi label="Integrations" value={integrations.length} />
        <Kpi
          label="Calls · 24h"
          value={apis.reduce((s, a) => s + a.calls24h, 0).toLocaleString()}
          delta="+6%"
          tone="success"
        />
        <Kpi label="p95 latency" value="286ms" delta="-14ms" tone="success" />
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
        <ApiTable items={filteredApis} onClone={cloneApi} onDelete={deleteApi} />
      ) : (
        <IntegrationsTable items={filteredIntegrations} onDelete={deleteIntegration} />
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
  return <span className={'ch-method ch-method-' + method.toLowerCase()}>{method}</span>;
}

function ApiTable({ items, onClone, onDelete }) {
  if (items.length === 0) return <EmptyTable label="No APIs match your filters" />;
  return (
    <div className="ch-table-wrap">
      <table className="ch-table">
        <thead>
          <tr>
            <th style={{ width: 72 }}>Method</th>
            <th>Name</th>
            <th>Integration</th>
            <th className="num">Calls · 24h</th>
            <th className="num">p95</th>
            <th>Last used</th>
            <th style={{ width: 220 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td><MethodBadge method={a.method} /></td>
              <td>
                <div className="ch-api-name">{a.name}</div>
                <div className="ch-api-desc">{a.description}</div>
              </td>
              <td>
                <span className="ch-chip">{a.integration}</span>
              </td>
              <td className="num">{a.calls24h.toLocaleString()}</td>
              <td className="num">{a.p95}</td>
              <td className="muted">{a.lastUsed}</td>
              <td>
                <div className="ch-row-actions">
                  <button className="ch-row-action"><Icon.Pencil /> Edit</button>
                  <button className="ch-row-action" onClick={() => onClone(a.id)}><Icon.Copy /> Clone</button>
                  <button className="ch-row-action"><Icon.Play /> Test</button>
                  <button className="ch-row-action danger" onClick={() => onDelete(a.id)}>
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

function IntegrationsTable({ items, onDelete }) {
  if (items.length === 0) return <EmptyTable label="No integrations match your search" />;
  return (
    <div className="ch-table-wrap">
      <table className="ch-table">
        <thead>
          <tr>
            <th>Name</th>
            <th className="num">APIs</th>
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
              <td className="num">{i.apis}</td>
              <td className="muted">{i.desc}</td>
              <td>
                <span
                  className={
                    'ch-pill ch-pill-' + (i.health === 'good' ? 'success' : 'warning')
                  }
                >
                  <span className="ch-pill-dot" />
                  {i.health === 'good' ? 'Operational' : 'Degraded'}
                </span>
              </td>
              <td>
                <div className="ch-row-actions">
                  <button className="ch-row-action"><Icon.Pencil /> Edit</button>
                  <button className="ch-row-action"><Icon.Code /> Show APIs</button>
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
