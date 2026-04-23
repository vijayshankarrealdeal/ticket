import { useEffect, useMemo, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { api } from '../api.js';

export default function Analytics() {
  const [summary, setSummary] = useState(null);
  const [metric, setMetric] = useState('tickets');
  const [series, setSeries] = useState([]);

  useEffect(() => {
    let alive = true;
    api.get('/analytics/summary')
      .then((s) => { if (alive) setSummary(s || null); })
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

  const stat = (key, fallback = '—') => summary?.[key] ?? fallback;

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
          <div className="stat">
            <div className="label">Tickets this week</div>
            <div className="value">{stat('ticketsThisWeek')}</div>
          </div>
          <div className="stat">
            <div className="label">Avg resolution</div>
            <div className="value">{stat('avgResolution')}</div>
          </div>
          <div className="stat">
            <div className="label">CSAT</div>
            <div className="value">{summary?.csat != null ? `${summary.csat}%` : '—'}</div>
          </div>
          <div className="stat">
            <div className="label">AI deflection</div>
            <div className="value">{summary?.aiDeflection != null ? `${summary.aiDeflection}%` : '—'}</div>
          </div>
        </div>

        <div className="card" style={{ padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>
              {metric === 'resolution' ? 'Avg resolution time (hours)'
                : metric === 'deflection' ? 'AI deflection %'
                : metric === 'csat' ? 'CSAT %'
                : 'Tickets per day'}
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {['tickets', 'resolution', 'deflection', 'csat'].map((m) => (
                <button
                  key={m}
                  className={'btn ' + (metric === m ? '' : 'secondary')}
                  style={{ padding: '4px 10px', fontSize: 12 }}
                  onClick={() => setMetric(m)}
                >
                  {m === 'tickets' ? 'Volume'
                    : m === 'resolution' ? 'Resolution'
                    : m === 'deflection' ? 'AI deflection'
                    : 'CSAT'}
                </button>
              ))}
            </div>
          </div>
          <BarChart series={series} />
        </div>
      </div>
    </>
  );
}

function BarChart({ series }) {
  const points = useMemo(() => series.slice(-14), [series]);
  const max = Math.max(1, ...points.map((p) => Number(p.value) || 0));
  const width = 720;
  const height = 220;
  const barGap = 6;
  const padX = 32;
  const padY = 24;
  const plotW = width - padX * 2;
  const plotH = height - padY * 2;
  const barW = points.length > 0 ? (plotW - barGap * (points.length - 1)) / points.length : 0;

  if (points.length === 0) {
    return (
      <div style={{ height, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>
        No data in this range yet.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} style={{ display: 'block' }}>
      {[0, 0.5, 1].map((t, i) => {
        const y = padY + plotH - plotH * t;
        return (
          <g key={i}>
            <line x1={padX} y1={y} x2={width - padX} y2={y} stroke="var(--border)" strokeDasharray="3 4" />
            <text x={padX - 6} y={y + 4} textAnchor="end" fontSize="10" fill="var(--muted)">
              {Math.round(max * t)}
            </text>
          </g>
        );
      })}
      {points.map((p, i) => {
        const v = Number(p.value) || 0;
        const h = (v / max) * plotH;
        const x = padX + i * (barW + barGap);
        const y = padY + plotH - h;
        return (
          <g key={p.day + i}>
            <rect x={x} y={y} width={barW} height={h} rx="3" fill="var(--primary, #1e40af)" />
            {i % 2 === 0 && (
              <text x={x + barW / 2} y={height - 6} textAnchor="middle" fontSize="10" fill="var(--muted)">
                {(p.day || '').slice(5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
