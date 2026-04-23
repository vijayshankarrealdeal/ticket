import { useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import TopBar from '../components/TopBar.jsx';
import { api } from '../api.js';

const BLUE = {
  50: '#eff6ff',
  100: '#dbeafe',
  200: '#bfdbfe',
  300: '#93c5fd',
  400: '#60a5fa',
  500: '#3b82f6',
  600: '#2563eb',
  700: '#1d4ed8',
  800: '#1e40af',
  900: '#1e3a8a',
};
const PRIMARY = BLUE[500];
const PALETTE = [BLUE[500], BLUE[300], BLUE[700], BLUE[400], BLUE[800], BLUE[200], BLUE[600], BLUE[900]];
const RESOLUTION_COLORS = { 'AI resolved': BLUE[500], 'Human resolved': BLUE[200] };
const CSAT_COLORS = [BLUE[100], BLUE[300], BLUE[400], BLUE[500], BLUE[700]];

const METRIC_LABEL = {
  tickets: 'Volume',
  resolution: 'Resolution',
  deflection: 'AI deflection',
  csat: 'CSAT',
};

export default function Analytics() {
  const [summary, setSummary] = useState(null);
  const [breakdown, setBreakdown] = useState(null);
  const [metric, setMetric] = useState('tickets');
  const [series, setSeries] = useState([]);

  useEffect(() => {
    let alive = true;
    api.get('/analytics/summary')
      .then((s) => { if (alive) setSummary(s || null); })
      .catch(() => {});
    api.get('/analytics/breakdown?range=14d')
      .then((b) => { if (alive) setBreakdown(b || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    api.get(`/analytics/timeseries?metric=${encodeURIComponent(metric)}&range=14d`)
      .then((r) => { if (alive) setSeries(Array.isArray(r?.series) ? r.series : []); })
      .catch(() => {});
    return () => { alive = false; };
  }, [metric]);

  const trendData = useMemo(
    () => series.map((p) => ({ day: (p.day || '').slice(5), value: Number(p.value) || 0 })),
    [series],
  );

  const resolutionMix = breakdown?.resolutionMix || [];
  const resolutionTotal = resolutionMix.reduce((s, p) => s + (p.value || 0), 0);
  const csatDistribution = breakdown?.csatDistribution || [];
  const channelMix = breakdown?.channelMix || [];
  const statusMix = (breakdown?.statusMix || []).filter((s) => s.value > 0);
  const priorityMix = (breakdown?.priorityMix || []).filter((s) => s.value > 0);

  return (
    <>
      <TopBar
        left={
          <>
            <div className="top-bar-title">Analytics</div>
            <span className="top-bar-subcount">Performance across channels and agents</span>
          </>
        }
      />
      <div className="page-scroll">
        <div className="stat-row">
          <Stat label="Tickets this week" value={summary?.ticketsThisWeek ?? '—'} />
          <Stat label="Avg resolution" value={summary?.avgResolution ?? '—'} />
          <Stat
            label="CSAT"
            value={summary?.csat != null ? `${summary.csat}%` : '—'}
            sub={breakdown?.csatResponses ? `${breakdown.csatResponses} responses` : null}
          />
          <Stat
            label="AI deflection"
            value={summary?.aiDeflection != null ? `${summary.aiDeflection}%` : '—'}
            sub={resolutionTotal ? `${resolutionTotal} resolved` : null}
          />
        </div>

        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <ChartHeader
            title={
              metric === 'resolution' ? 'Avg resolution time (hours)'
                : metric === 'deflection' ? 'AI deflection %'
                : metric === 'csat' ? 'CSAT %'
                : 'Tickets per day'
            }
            right={
              <div style={{ display: 'flex', gap: 6 }}>
                {Object.keys(METRIC_LABEL).map((m) => (
                  <button
                    key={m}
                    className={'btn ' + (metric === m ? '' : 'secondary')}
                    style={{ padding: '4px 10px', fontSize: 12 }}
                    onClick={() => setMetric(m)}
                  >
                    {METRIC_LABEL[m]}
                  </button>
                ))}
              </div>
            }
          />
          <div style={{ height: 260 }}>
            {trendData.length === 0 ? (
              <Empty />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trendData} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
                  <defs>
                    <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={PRIMARY} stopOpacity={0.45} />
                      <stop offset="100%" stopColor={PRIMARY} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                  <XAxis dataKey="day" stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={40} />
                  <Tooltip content={<DarkTooltip />} />
                  <Area type="monotone" dataKey="value" stroke={PRIMARY} strokeWidth={2} fill="url(#trendFill)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
            gap: 16,
            marginBottom: 16,
          }}
        >
          <div className="card" style={{ padding: 16 }}>
            <ChartHeader
              title="Resolution mix"
              subtitle={resolutionTotal ? `${resolutionTotal} resolved · last 14d` : 'last 14d'}
            />
            <div style={{ height: 240 }}>
              {resolutionTotal === 0 ? (
                <Empty />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={resolutionMix}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={60}
                      outerRadius={92}
                      paddingAngle={2}
                      stroke="var(--panel)"
                    >
                      {resolutionMix.map((entry) => (
                        <Cell key={entry.name} fill={RESOLUTION_COLORS[entry.name] || PRIMARY} />
                      ))}
                    </Pie>
                    <Tooltip content={<DarkTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, color: 'var(--text-2)' }} />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="card" style={{ padding: 16 }}>
            <ChartHeader
              title="CSAT ratings"
              subtitle={
                breakdown?.csatSatisfiedPct != null
                  ? `${breakdown.csatSatisfiedPct}% satisfied (4–5★)`
                  : 'last 14d'
              }
            />
            <div style={{ height: 240 }}>
              {csatDistribution.every((d) => !d.count) ? (
                <Empty />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={csatDistribution} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                    <XAxis dataKey="rating" stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false}
                      tickFormatter={(r) => `${r}★`} />
                    <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
                    <Tooltip content={<DarkTooltip />} />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                      {csatDistribution.map((_, i) => (
                        <Cell key={i} fill={CSAT_COLORS[i]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="card" style={{ padding: 16 }}>
            <ChartHeader title="Tickets by channel" subtitle="last 14d" />
            <div style={{ height: 240 }}>
              {channelMix.length === 0 ? (
                <Empty />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={channelMix}
                    layout="vertical"
                    margin={{ top: 4, right: 24, left: 8, bottom: 0 }}
                  >
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" horizontal={false} />
                    <XAxis type="number" stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                    <YAxis
                      type="category"
                      dataKey="channel"
                      stroke="var(--muted)"
                      tick={{ fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      width={80}
                    />
                    <Tooltip content={<DarkTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, color: 'var(--text-2)' }} />
                    <Bar dataKey="resolved" stackId="a" fill={BLUE[600]} radius={[0, 0, 0, 0]} />
                    <Bar dataKey="open" stackId="a" fill={BLUE[200]} radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="card" style={{ padding: 16 }}>
            <ChartHeader title="Status mix" subtitle="last 14d" />
            <div style={{ height: 240 }}>
              {statusMix.length === 0 ? (
                <Empty />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={statusMix}
                      dataKey="value"
                      nameKey="name"
                      outerRadius={92}
                      paddingAngle={2}
                      stroke="var(--panel)"
                    >
                      {statusMix.map((entry, i) => (
                        <Cell key={entry.name} fill={PALETTE[i % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip content={<DarkTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, color: 'var(--text-2)' }} />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {priorityMix.length > 0 && (
            <div className="card" style={{ padding: 16 }}>
              <ChartHeader title="Priority mix" subtitle="last 14d" />
              <div style={{ height: 240 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={priorityMix} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                    <XAxis dataKey="name" stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                    <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
                    <Tooltip content={<DarkTooltip />} />
                    <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                      {priorityMix.map((entry, i) => (
                        <Cell key={entry.name} fill={PALETTE[i % PALETTE.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub ? <div className="trend" style={{ color: 'var(--muted)' }}>{sub}</div> : null}
    </div>
  );
}

function ChartHeader({ title, subtitle, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
      <div>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div>
        {subtitle ? <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{subtitle}</div> : null}
      </div>
      {right}
    </div>
  );
}

function Empty() {
  return (
    <div style={{ height: '100%', display: 'grid', placeItems: 'center', color: 'var(--muted)', fontSize: 13 }}>
      No data in this range yet.
    </div>
  );
}

function DarkTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: 'var(--panel)',
        border: '1px solid var(--border-strong)',
        borderRadius: 8,
        padding: '8px 10px',
        fontSize: 12,
        color: 'var(--text)',
        boxShadow: '0 6px 20px rgba(0,0,0,0.25)',
      }}
    >
      {label != null && <div style={{ color: 'var(--muted)', marginBottom: 4 }}>{label}</div>}
      {payload.map((p, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color || p.fill }} />
          <span style={{ color: 'var(--text-2)' }}>{p.name}:</span>
          <strong>{typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</strong>
        </div>
      ))}
    </div>
  );
}
