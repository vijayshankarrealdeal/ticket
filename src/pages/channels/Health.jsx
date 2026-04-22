import { useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';

const services = [
  {
    id: 'shopify',
    name: 'Shopify',
    category: 'E-commerce',
    status: 'operational',
    uptime30d: 99.98,
    latencyP95: 210,
    errorRate: 0.01,
    lastCheck: '12s ago',
    accent: '#95bf47',
    letter: 'S',
  },
  {
    id: 'unicommerce',
    name: 'Unicommerce',
    category: 'OMS',
    status: 'degraded',
    uptime30d: 98.4,
    latencyP95: 1240,
    errorRate: 1.3,
    lastCheck: '7s ago',
    accent: '#9ab04a',
    letter: 'U',
  },
  {
    id: 'shiprocket',
    name: 'Shiprocket',
    category: 'Shipping',
    status: 'operational',
    uptime30d: 99.92,
    latencyP95: 420,
    errorRate: 0.05,
    lastCheck: '4s ago',
    accent: '#7c3aed',
    letter: 'SR',
  },
  {
    id: 'delhivery',
    name: 'Delhivery webhook',
    category: 'Shipping',
    status: 'operational',
    uptime30d: 99.88,
    latencyP95: 310,
    errorRate: 0.04,
    lastCheck: '9s ago',
    accent: '#111827',
    letter: 'D',
  },
  {
    id: 'api',
    name: 'Agent runtime API',
    category: 'Internal',
    status: 'operational',
    uptime30d: 100,
    latencyP95: 180,
    errorRate: 0,
    lastCheck: '3s ago',
    accent: '#1e40af',
    letter: 'AR',
  },
  {
    id: 'sheets',
    name: 'Google Sheets sync',
    category: 'Data source',
    status: 'operational',
    uptime30d: 99.6,
    latencyP95: 780,
    errorRate: 0.2,
    lastCheck: '18s ago',
    accent: '#16a34a',
    letter: 'GS',
  },
];

const incidents = [
  {
    id: 'inc-102',
    title: 'Unicommerce webhook retries elevated',
    status: 'monitoring',
    tone: 'warning',
    started: '26 minutes ago',
    components: ['Unicommerce'],
    summary:
      'We are seeing a 1.3% error rate on inbound inventory webhooks. Retries are succeeding on the second attempt.',
  },
  {
    id: 'inc-101',
    title: 'Shopify rate limit briefly exceeded',
    status: 'resolved',
    tone: 'success',
    started: 'Yesterday at 4:22 PM',
    components: ['Shopify'],
    summary:
      'GraphQL calls hit the throttle bucket during a bulk price update. Mitigation: exponential backoff was applied automatically.',
  },
  {
    id: 'inc-100',
    title: 'Shiprocket tracking delay',
    status: 'resolved',
    tone: 'success',
    started: '2 days ago',
    components: ['Shiprocket'],
    summary:
      'Upstream tracking events were delayed by 12 minutes. Automatically caught up after the carrier feed recovered.',
  },
];

const COMPONENT_FILTERS = [
  { key: 'all', label: 'All components' },
  { key: 'operational', label: 'Operational' },
  { key: 'degraded', label: 'Degraded' },
  { key: 'outage', label: 'Outage' },
];

const WINDOWS = ['24h', '7d', '30d', '90d'];

export default function Health() {
  const [filter, setFilter] = useState('all');
  const [window, setWindow] = useState('30d');

  const filtered = useMemo(() => {
    if (filter === 'all') return services;
    return services.filter((s) => s.status === filter);
  }, [filter]);

  const counts = useMemo(() => {
    return {
      total: services.length,
      operational: services.filter((s) => s.status === 'operational').length,
      degraded: services.filter((s) => s.status === 'degraded').length,
      outage: services.filter((s) => s.status === 'outage').length,
    };
  }, []);

  const overall =
    counts.outage > 0
      ? { label: 'Partial outage', tone: 'danger' }
      : counts.degraded > 0
      ? { label: 'Degraded performance', tone: 'warning' }
      : { label: 'All systems operational', tone: 'success' };

  const avgUptime =
    services.reduce((s, x) => s + x.uptime30d, 0) / services.length;
  const avgLatency =
    services.reduce((s, x) => s + x.latencyP95, 0) / services.length;

  return (
    <div className="ch-page">
      <div className="ch-page-head">
        <div>
          <div className="ch-breadcrumb">
            <span>Channels</span>
            <Icon.Chevron className="ch-breadcrumb-sep" />
            <span className="ch-breadcrumb-current">Health</span>
          </div>
          <h1 className="ch-page-title">System health</h1>
          <p className="ch-page-sub">
            Live status of every connected integration, webhook and internal service.
          </p>
        </div>
        <div className="ch-page-head-actions">
          <button className="btn secondary"><Icon.Refresh /> Re-run checks</button>
          <button className="btn secondary"><Icon.Bell /> Subscribe</button>
        </div>
      </div>

      <div className={'ch-status-banner tone-' + overall.tone}>
        <div className="ch-status-banner-left">
          <div className="ch-status-banner-dot" />
          <div>
            <div className="ch-status-banner-title">{overall.label}</div>
            <div className="ch-status-banner-sub">
              {counts.operational} of {counts.total} components healthy · updated seconds ago
            </div>
          </div>
        </div>
        <div className="ch-status-window">
          {WINDOWS.map((w) => (
            <button
              key={w}
              className={'ch-status-window-btn' + (window === w ? ' active' : '')}
              onClick={() => setWindow(w)}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      <div className="ch-kpi-row">
        <Kpi label="Components" value={counts.total} />
        <Kpi
          label="Operational"
          value={counts.operational}
          tone="success"
          delta={`${Math.round((counts.operational / counts.total) * 100)}%`}
        />
        <Kpi label="Avg uptime · 30d" value={avgUptime.toFixed(2) + '%'} tone="success" />
        <Kpi label="Avg p95 latency" value={Math.round(avgLatency) + 'ms'} tone="success" delta="-24ms" />
      </div>

      <div className="ch-toolbar">
        <div className="ch-tabs">
          {COMPONENT_FILTERS.map((f) => (
            <button
              key={f.key}
              className={'ch-tab' + (filter === f.key ? ' active' : '')}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              <span className="ch-tab-count">
                {f.key === 'all'
                  ? counts.total
                  : f.key === 'operational'
                  ? counts.operational
                  : f.key === 'degraded'
                  ? counts.degraded
                  : counts.outage}
              </span>
            </button>
          ))}
        </div>
      </div>

      <section className="ch-section">
        <div className="ch-section-head">
          <div className="ch-section-title">
            <span className="ch-section-icon"><Icon.Heartbeat /></span>
            Components
            <span className="ch-section-count">{filtered.length}</span>
          </div>
        </div>
        <div className="ch-health-list">
          {filtered.map((s) => (
            <ServiceRow key={s.id} service={s} />
          ))}
          {filtered.length === 0 && (
            <div className="ch-empty inline">
              <div className="ch-empty-title">No components in this state</div>
              <div className="ch-empty-sub">Everything looks clean here right now.</div>
            </div>
          )}
        </div>
      </section>

      <section className="ch-section">
        <div className="ch-section-head">
          <div className="ch-section-title">
            <span className="ch-section-icon"><Icon.AlertTriangle /></span>
            Incidents &amp; changes
            <span className="ch-section-count">{incidents.length}</span>
          </div>
          <div className="ch-section-head-right">
            <button className="ch-link">Incident history</button>
          </div>
        </div>
        <div className="ch-incident-list">
          {incidents.map((inc) => (
            <IncidentRow key={inc.id} incident={inc} />
          ))}
        </div>
      </section>
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

function ServiceRow({ service }) {
  const statusMeta = {
    operational: { label: 'Operational', tone: 'success' },
    degraded: { label: 'Degraded', tone: 'warning' },
    outage: { label: 'Outage', tone: 'danger' },
  }[service.status];

  const bars = Array.from({ length: 30 }, (_, i) => {
    const seed = (service.id.charCodeAt(0) + i) % 17;
    if (service.status === 'outage' && i > 24) return 'danger';
    if (service.status === 'degraded' && (seed === 3 || i === 27)) return 'warning';
    if (seed === 7) return 'warning';
    return 'success';
  });

  return (
    <div className="ch-health-row">
      <div className="ch-health-row-left">
        <div className="ch-card-logo" style={{ background: service.accent }}>
          {service.letter}
        </div>
        <div>
          <div className="ch-store-name">{service.name}</div>
          <div className="ch-store-meta">{service.category} · Checked {service.lastCheck}</div>
        </div>
      </div>

      <div className="ch-health-bars" aria-label={`Uptime last 30 days for ${service.name}`}>
        {bars.map((tone, i) => (
          <span key={i} className={'ch-uptime-bar tone-' + tone} title={`Day ${30 - i}`} />
        ))}
      </div>

      <div className="ch-health-metrics">
        <Metric label="Uptime · 30d" value={service.uptime30d.toFixed(2) + '%'} tone="success" />
        <Metric label="p95" value={service.latencyP95 + 'ms'} />
        <Metric
          label="Error rate"
          value={service.errorRate + '%'}
          tone={service.errorRate > 1 ? 'warning' : service.errorRate > 0.1 ? 'muted' : 'success'}
        />
      </div>

      <div className="ch-health-status">
        <span className={'ch-pill ch-pill-' + statusMeta.tone}>
          <span className="ch-pill-dot" /> {statusMeta.label}
        </span>
      </div>
    </div>
  );
}

function Metric({ label, value, tone }) {
  return (
    <div className="ch-metric">
      <div className="ch-metric-label">{label}</div>
      <div className={'ch-metric-value' + (tone ? ' tone-' + tone : '')}>{value}</div>
    </div>
  );
}

function IncidentRow({ incident }) {
  const statusMeta = {
    investigating: { label: 'Investigating', tone: 'danger' },
    identified: { label: 'Identified', tone: 'warning' },
    monitoring: { label: 'Monitoring', tone: 'warning' },
    resolved: { label: 'Resolved', tone: 'success' },
  }[incident.status];
  return (
    <div className="ch-incident-row">
      <div className="ch-incident-left">
        <div className={'ch-incident-dot tone-' + incident.tone} />
        <div>
          <div className="ch-incident-title">{incident.title}</div>
          <div className="ch-incident-sub">
            {incident.components.join(', ')} · Started {incident.started}
          </div>
          <div className="ch-incident-body">{incident.summary}</div>
        </div>
      </div>
      <div className="ch-incident-right">
        <span className={'ch-pill ch-pill-' + statusMeta.tone}>
          <span className="ch-pill-dot" /> {statusMeta.label}
        </span>
        <button className="ch-row-action"><Icon.ExternalLink /> Details</button>
      </div>
    </div>
  );
}
