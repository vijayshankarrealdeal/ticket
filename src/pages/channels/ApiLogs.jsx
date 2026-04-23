import { useEffect, useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { api } from '../../api.js';

const STATUS_FILTERS = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'success', label: 'Success', match: (s) => String(s).startsWith('2') },
  { key: 'client', label: '4xx errors', match: (s) => String(s).startsWith('4') },
  { key: 'server', label: '5xx errors', match: (s) => String(s).startsWith('5') },
];

export default function ApiLogs() {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [apiFilter, setApiFilter] = useState('ALL');
  const [selected, setSelected] = useState(null);
  const [logs, setLogs] = useState([]);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.get('/api-logs').catch(() => []),
      api.get('/api-logs/stats').catch(() => null),
    ]).then(([l, s]) => {
      if (!alive) return;
      setLogs(Array.isArray(l) ? l : l?.items || []);
      setStats(s || null);
    });
    return () => { alive = false; };
  }, []);

  const apiOptions = useMemo(
    () => ['ALL', ...Array.from(new Set(logs.map((l) => l.apiName).filter(Boolean)))],
    [logs],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filter = STATUS_FILTERS.find((f) => f.key === status).match;
    return logs.filter((l) => {
      if (!filter(l.status)) return false;
      if (apiFilter !== 'ALL' && l.apiName !== apiFilter) return false;
      if (!q) return true;
      return (
        (l.apiName || '').toLowerCase().includes(q) ||
        (l.customerId || '').toLowerCase().includes(q) ||
        (l.executeAction || '').toLowerCase().includes(q)
      );
    });
  }, [query, status, apiFilter, logs]);

  const counts = useMemo(() => {
    if (stats) return {
      total: stats.total ?? logs.length,
      success: stats.success ?? 0,
      err4: stats.err4 ?? 0,
      err5: stats.err5 ?? 0,
    };
    const total = logs.length;
    const success = logs.filter((l) => String(l.status).startsWith('2')).length;
    const err4 = logs.filter((l) => String(l.status).startsWith('4')).length;
    const err5 = logs.filter((l) => String(l.status).startsWith('5')).length;
    return { total, success, err4, err5 };
  }, [logs, stats]);

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
          <button className="btn secondary"><Icon.Refresh /> Refresh</button>
          <button className="btn secondary"><Icon.ExternalLink /> Export CSV</button>
        </div>
      </div>

      <div className="ch-kpi-row">
        <Kpi label="Requests · 24h" value={counts.total} />
        <Kpi label="Success rate" value={`${Math.round((counts.success / counts.total) * 100)}%`} tone="success" delta="+1.2%" />
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
          <DateRange />
          <div className="ch-search">
            <Icon.Search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by API, customer or action"
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
                <th>Execute action</th>
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
                    <td className="muted">{l.time}</td>
                    <td><span className="ch-api-name">{l.apiName}</span></td>
                    <td><span className="ch-chip">{l.executeAction}</span></td>
                    <td className="muted">{l.customerId}</td>
                    <td><StatusPill code={l.status} /></td>
                    <td className="num">{l.latency}ms</td>
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
                  {selected.timestamp.toLocaleString()} · {selected.customerId}
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
                <div className="ch-log-meta-value">{selected.latency}ms</div>
              </div>
              <div>
                <div className="ch-log-meta-label">Action</div>
                <div className="ch-log-meta-value">{selected.executeAction}</div>
              </div>
            </div>
            <div className="ch-log-section">
              <div className="ch-log-section-title">Request</div>
              <pre className="ch-code-block">{`POST /api/v1/${selected.executeAction}
Host: api.workspace.io
Authorization: Bearer ••••
Content-Type: application/json

{
  "customer_id": "${selected.customerId}",
  "source": "agent_runtime",
  "trace_id": "${selected.id}"
}`}</pre>
            </div>
            <div className="ch-log-section">
              <div className="ch-log-section-title">Response</div>
              <pre className="ch-code-block">{selected.status.startsWith('2')
                ? `${selected.status} OK

{
  "ok": true,
  "duration_ms": ${selected.latency},
  "data": { "matched": 12 }
}`
                : `${selected.status} ${selected.status.startsWith('4') ? 'Bad Request' : 'Internal Server Error'}

{
  "error": "${selected.status.startsWith('4') ? 'invalid_request' : 'upstream_failure'}",
  "message": "See runbook for remediation steps."
}`}</pre>
            </div>
          </aside>
        )}
      </div>
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

function DateRange() {
  return (
    <button className="ch-date-range" type="button">
      <Icon.Calendar />
      <span>Last 24h</span>
      <Icon.Chevron />
    </button>
  );
}

function StatusPill({ code }) {
  const tone = code.startsWith('2') ? 'success' : code.startsWith('4') ? 'warning' : 'danger';
  const label = code.startsWith('2') ? 'Success' : code.startsWith('4') ? 'Client' : 'Server';
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
