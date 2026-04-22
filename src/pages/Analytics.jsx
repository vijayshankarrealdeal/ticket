import TopBar from '../components/TopBar.jsx';

export default function Analytics() {
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
          <div className="stat"><div className="label">Tickets this week</div><div className="value">842</div><div className="trend">+12%</div></div>
          <div className="stat"><div className="label">Avg resolution</div><div className="value">1h 42m</div><div className="trend">-8%</div></div>
          <div className="stat"><div className="label">CSAT</div><div className="value">4.6 / 5</div><div className="trend">+0.2</div></div>
          <div className="stat"><div className="label">AI deflection</div><div className="value">64%</div><div className="trend">+5%</div></div>
        </div>
        <div className="card" style={{ height: 320, display: 'grid', placeItems: 'center', color: 'var(--muted)' }}>
          Charts coming soon
        </div>
      </div>
    </>
  );
}
