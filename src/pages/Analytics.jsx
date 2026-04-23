import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { api } from '../api.js';

export default function Analytics() {
  const [summary, setSummary] = useState(null);

  useEffect(() => {
    let alive = true;
    api.get('/analytics/summary')
      .then((s) => { if (alive) setSummary(s || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const stat = (key, fallback = '—') => summary?.[key] ?? fallback;

  return (
    <>
      <TopBar left={<div className="top-bar-title">Analytics</div>} />
      <div className="page-scroll">
        <div className="page-header">
          <div>
            <div className="page-title">Analytics</div>
            <div className="page-sub">Performance metrics across channels and agents.</div>
          </div>
        </div>
        <div className="stat-row">
          <div className="stat">
            <div className="label">Tickets this week</div>
            <div className="value">{stat('ticketsThisWeek')}</div>
            {summary?.ticketsTrend && <div className="trend">{summary.ticketsTrend}</div>}
          </div>
          <div className="stat">
            <div className="label">Avg resolution</div>
            <div className="value">{stat('avgResolution')}</div>
            {summary?.avgResolutionTrend && <div className="trend">{summary.avgResolutionTrend}</div>}
          </div>
          <div className="stat">
            <div className="label">CSAT</div>
            <div className="value">{stat('csat')}</div>
            {summary?.csatTrend && <div className="trend">{summary.csatTrend}</div>}
          </div>
          <div className="stat">
            <div className="label">AI deflection</div>
            <div className="value">{stat('aiDeflection')}</div>
            {summary?.aiDeflectionTrend && <div className="trend">{summary.aiDeflectionTrend}</div>}
          </div>
        </div>
        <div className="card" style={{ height: 320, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>
          Charts coming soon
        </div>
      </div>
    </>
  );
}
