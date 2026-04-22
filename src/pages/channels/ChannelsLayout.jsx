import { NavLink, Outlet } from 'react-router-dom';
import TopBar from '../../components/TopBar.jsx';
import { Icon } from '../../components/Icons.jsx';
import './channels.css';

const sections = [
  { to: '/channels', label: 'Channels', icon: Icon.Plug, end: true, desc: 'E-commerce & shipping' },
  { to: '/channels/ecommerce', label: 'E-Commerce', icon: Icon.ShoppingBag, desc: 'Stores & catalogs' },
  { to: '/channels/api-library', label: 'API Library', icon: Icon.Code, desc: 'APIs & integrations' },
  { to: '/channels/api-logs', label: 'API Logs', icon: Icon.Activity, desc: 'Execution history' },
  { to: '/channels/sheets', label: 'Google Sheets', icon: Icon.Sheet, desc: 'Connected spreadsheets' },
  { to: '/channels/health', label: 'Health', icon: Icon.Heartbeat, desc: 'Uptime & incidents' },
];

export default function ChannelsLayout() {
  return (
    <>
      <TopBar left={<div className="top-bar-title">Channels</div>} />
      <div className="ch-layout">
        <aside className="ch-sidenav" aria-label="Channel sections">
          <div className="ch-sidenav-head">
            <div className="ch-sidenav-title">Workspace</div>
            <div className="ch-sidenav-sub">Everything connected</div>
          </div>
          <nav className="ch-sidenav-list">
            {sections.map((s) => {
              const I = s.icon;
              return (
                <NavLink
                  key={s.to}
                  to={s.to}
                  end={s.end}
                  className={({ isActive }) =>
                    'ch-sidenav-item' + (isActive ? ' active' : '')
                  }
                >
                  <span className="ch-sidenav-icon"><I /></span>
                  <span className="ch-sidenav-body">
                    <span className="ch-sidenav-label">{s.label}</span>
                    <span className="ch-sidenav-desc">{s.desc}</span>
                  </span>
                </NavLink>
              );
            })}
          </nav>
        </aside>
        <div className="ch-content">
          <Outlet />
        </div>
      </div>
    </>
  );
}
