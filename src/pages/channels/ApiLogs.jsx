import { useEffect, useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { Spinner } from '../../components/Spinner.jsx';
import { api } from '../../api.js';

const STATUS_FILTERS = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'success', label: 'Success', match: (s) => s >= 200 && s < 300 },
  { key: 'client', label: '4xx errors', match: (s) => s >= 400 && s < 500 },
  { key: 'server', label: '5xx errors', match: (s) => s >= 500 },
];

function relTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const secs = Math.max(1, Math.round((Date.now() - d.getTime()) / 1000));
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  } catch { return '—'; }
}

export default function ApiLogs() {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [apiFilter, setApiFilter] = useState('ALL');
  const [selected, setSelected] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [logs, setLogs] = useState([]);
  const [stats, setStats] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);

  async function refresh() {
    const [l, s] = await Promise.all([
      api.get('/api-logs').catch(() => []),
      api.get('/api-logs/stats').catch(() => null),
    ]);
    setLogs(Array.isArray(l) ? l : l?.items || []);
    setStats(s || null);
  }

  useEffect(() => {
    refresh().finally(() => setInitialLoading(false));
  }, []);

  useEffect(() => {
    if (!selected) { setSelectedDetail(null); return; }
    let alive = true;
    api.get(`/api-logs/${encodeURIComponent(selected.id)}`)
      .then((d) => { if (alive) setSelectedDetail(d); })
      .catch(() => { if (alive) setSelectedDetail({ error: 'failed to load detail' }); });
    return () => { alive = false; };
  }, [selected?.id]);

  const apiOptions = useMemo(
    () => ['ALL', ...Array.from(new Set(logs.map((l) => l.apiName).filter((n) => n && n !== '—')))],
    [logs],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filter = STATUS_FILTERS.find((f) => f.key === status).match;
    return logs.filter((l) => {
      if (!filter(Number(l.status))) return false;
      if (apiFilter !== 'ALL' && l.apiName !== apiFilter) return false;
      if (!q) return true;
      return (
        (l.apiName || '').toLowerCase().includes(q) ||
        (l.customerId || '').toLowerCase().includes(q) ||
        (l.traceId || '').toLowerCase().includes(q)
      );
    });
  }, [query, status, apiFilter, logs]);

  const counts = useMemo(() => {
    if (stats) {
      return {
        total: stats.total ?? 0,
        success: stats.success ?? stats.breakdown?.['2xx'] ?? 0,
        err4: stats.breakdown?.['4xx'] ?? 0,
        err5: stats.breakdown?.['5xx'] ?? 0,
      };
    }
    const total = logs.length;
    const success = logs.filter((l) => Number(l.status) >= 200 && Number(l.status) < 300).length;
    const err4 = logs.filter((l) => Number(l.status) >= 400 && Number(l.status) < 500).length;
    const err5 = logs.filter((l) => Number(l.status) >= 500).length;
    return { total, success, err4, err5 };
  }, [logs, stats]);

  const successRate = counts.total ? Math.round((counts.success / counts.total) * 100) : 100;

  const downloadCsv = () => {
    const headers = ['at', 'apiName', 'customerId', 'traceId', 'status', 'latencyMs'];
    const body = filtered
      .map((l) => headers.map((h) => JSON.stringify(l[h] ?? '')).join(','))
      .join('\n');
    const blob = new Blob([headers.join(',') + '\n' + body], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'api-logs.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const downloadNdjson = async () => {
    // Streams direct from the server so the export is *authoritative* — it
    // pulls up to `limit` rows from the DB, bypassing the in-memory filter
    // that CSV export uses. Uses the wx_session cookie for auth.
    const base = import.meta.env.VITE_API_URL || '/api';
    const url = `${base}/api-logs/stream?limit=5000`;
    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'api-logs.ndjson';
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      alert(`Download failed: ${e?.message || e}`);
    }
  };

  return (
    <div className="ch-page">
      <div className="ch-page-head">
        <div>
          <div className="ch-breadcrumb">
            <span>Channels</span>
            <Icon.Chevron className="ch-breadcrumb-sep" />
            <span className="ch-breadcrumb-current">API Logs</span>
          </div>
          <h1 className="ch-page-title">API execution logs</h1>
          <p className="ch-page-sub">
            Trace every API invocation made by your automations, agents and integrations.
          </p>
        </div>
        <div className="ch-page-head-actions">
          <button className="btn secondary" onClick={refresh}><Icon.Refresh /> Refresh</button>
          <button className="btn secondary" onClick={downloadCsv}><Icon.ExternalLink /> Export CSV</button>
          <button className="btn secondary" onClick={downloadNdjson} title="Server-streamed NDJSON, up to 5000 rows">
            <Icon.ExternalLink /> Export NDJSON
          </button>
        </div>
      </div>

      {initialLoading && logs.length === 0 ? (
        <div style={{ padding: '80px 16px', display: 'flex', justifyContent: 'center' }}>
          <Spinner size={32} />
        </div>
      ) : (
      <>
      <div className="ch-kpi-row">
        <Kpi label="Requests · 24h" value={counts.total} />
        <Kpi label="Success rate" value={`${successRate}%`} tone="success" />
        <Kpi label="4xx errors" value={counts.err4} tone="warning" />
        <Kpi label="5xx errors" value={counts.err5} tone="danger" />
      </div>

      <div className="ch-toolbar">
        <div className="ch-tabs">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              className={'ch-tab' + (status === f.key ? ' active' : '')}
              onClick={() => setStatus(f.key)}
            >
              {f.label}
              <span className="ch-tab-count">
                {f.key === 'all' ? counts.total : f.key === 'success' ? counts.success : f.key === 'client' ? counts.err4 : counts.err5}
              </span>
            </button>
          ))}
        </div>
        <div className="ch-toolbar-right">
          <Dropdown
            label="API"
            value={apiFilter}
            options={apiOptions.map((o) => ({ value: o, label: o }))}
            onChange={setApiFilter}
          />
          <div className="ch-search">
            <Icon.Search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by API, customer or trace id"
            />
            {query && (
              <button className="ch-search-clear" onClick={() => setQuery('')}>
                <Icon.Close />
              </button>
            )}
          </div>
        </div>
      </div>

      <div className={'ch-logs-layout' + (selected ? ' with-detail' : '')}>
        <div className="ch-table-wrap">
          <table className="ch-table">
            <thead>
              <tr>
                <th style={{ width: 140 }}>Time</th>
                <th>API</th>
                <th>Trace</th>
                <th>Customer ID</th>
                <th style={{ width: 110 }}>Status</th>
                <th className="num" style={{ width: 100 }}>Latency</th>
                <th style={{ width: 90 }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <EmptyLogs />
                  </td>
                </tr>
              ) : (
                filtered.map((l) => (
                  <tr
                    key={l.id}
                    className={'ch-log-row' + (selected?.id === l.id ? ' active' : '')}
                    onClick={() => setSelected(l)}
                  >
                    <td className="muted">{relTime(l.at)}</td>
                    <td><span className="ch-api-name">{l.apiName}</span></td>
                    <td><span className="ch-chip" style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{l.traceId}</span></td>
                    <td className="muted">{l.customerId || '—'}</td>
                    <td><StatusPill code={l.status} /></td>
                    <td className="num">{l.latencyMs}ms</td>
                    <td>
                      <button className="ch-row-action">View</button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {selected && (
          <aside className="ch-log-detail">
            <div className="ch-log-detail-head">
              <div>
                <div className="ch-log-detail-title">{selected.apiName}</div>
                <div className="ch-log-detail-sub">
                  {selected.at ? new Date(selected.at).toLocaleString() : '—'} · {selected.customerId || '—'}
                </div>
              </div>
              <button className="ch-icon-btn" onClick={() => setSelected(null)} title="Close">
                <Icon.Close />
              </button>
            </div>
            <div className="ch-log-detail-meta">
              <div>
                <div className="ch-log-meta-label">Status</div>
                <StatusPill code={selected.status} />
              </div>
              <div>
                <div className="ch-log-meta-label">Latency</div>
                <div className="ch-log-meta-value">{selected.latencyMs}ms</div>
              </div>
              <div>
                <div className="ch-log-meta-label">Trace</div>
                <div className="ch-log-meta-value" style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>
                  {selected.traceId}
                </div>
              </div>
            </div>
            <div className="ch-log-section">
              <div className="ch-log-section-title">Request</div>
              <pre className="ch-code-block">{
                selectedDetail
                  ? selectedDetail.error
                    ? `// ${selectedDetail.error}`
                    : JSON.stringify(selectedDetail.request, null, 2)
                  : '// loading…'
              }</pre>
            </div>
            <div className="ch-log-section">
              <div className="ch-log-section-title">Response</div>
              <pre className="ch-code-block">{
                selectedDetail
                  ? selectedDetail.error
                    ? ''
                    : JSON.stringify(selectedDetail.response, null, 2)
                  : '// loading…'
              }</pre>
            </div>
          </aside>
        )}
      </div>
      </>
      )}
    </div>
  );
}

function Kpi({ label, value, delta, tone }) {
  return (
    <div className="ch-kpi">
      <div className="ch-kpi-label">{label}</div>
      <div className="ch-kpi-value">{value}</div>
      {delta && <div className={'ch-kpi-delta tone-' + (tone || 'success')}>{delta}</div>}
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

function StatusPill({ code }) {
  const n = Number(code);
  const tone = n >= 200 && n < 300 ? 'success' : n >= 400 && n < 500 ? 'warning' : 'danger';
  const label = n >= 200 && n < 300 ? 'Success' : n >= 400 && n < 500 ? 'Client' : 'Server';
  return (
    <span className={'ch-status-pill tone-' + tone}>
      <span className="ch-status-code">{code}</span>
      <span className="ch-status-label">{label}</span>
    </span>
  );
}

function EmptyLogs() {
  return (
    <div className="ch-table-empty">
      <Icon.InboxEmpty />
      <div className="ch-empty-title">No executions found</div>
      <div className="ch-empty-sub">Try widening your date range or clearing filters.</div>
    </div>
  );
}
