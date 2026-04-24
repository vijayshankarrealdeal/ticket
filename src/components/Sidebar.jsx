import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Icon } from './Icons.jsx';
import { useTheme } from '../hooks/useTheme.js';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { useWorkspaceCounters } from '../hooks/useWorkspaceCounters.js';
import { PRODUCT_NAME } from '../config.js';

const CHANNELS = [
  { key: 'All', label: 'All' },
  { key: 'WhatsApp', label: 'Whatsapp' },
  { key: 'Instagram', label: 'Instagram' },
  { key: 'Live Chat', label: 'Live Chat' },
  { key: 'Mails', label: 'Mails' },
];

const CHANNEL_SUB = [
  { to: '/channels', label: 'Channels', end: true },
  { to: '/channels/ecommerce', label: 'E-Commerce' },
  { to: '/channels/api-library', label: 'API Library' },
  { to: '/channels/api-logs', label: 'API Logs' },
  { to: '/channels/sheets', label: 'Google Sheets' },
  { to: '/channels/health', label: 'Health' },
];

const items = [
  { to: '/conversations', icon: Icon.Inbox, label: 'Inbox', expandable: true },
  { to: '/tickets', icon: Icon.Ticket, label: 'Tickets' },
  { to: '/customers', icon: Icon.Users, label: 'Customers' },
  { to: '/teams', icon: Icon.Teams, label: 'Teams' },
  { to: '/notifications', icon: Icon.Bell, label: 'Notifications' },
  { to: '/analytics', icon: Icon.Chart, label: 'Insights', expandable: true },
  { to: '/agent-builder', icon: Icon.AiAgent, label: 'Make Agents', expandable: true },
  { to: '/api-playground', icon: Icon.Bolt, label: 'API Playground' },
  { to: '/channels', icon: Icon.Plug, label: 'Channels', expandable: true },
];

const EXPANDED_KEY = 'workspacex-sidebar-expanded';

export default function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const counters = useWorkspaceCounters();
  const BADGE_BY_LABEL = {
    Inbox: counters.conversationsUnread,
    Tickets: counters.ticketsOpen,
    Notifications: counters.notificationsUnread,
  };

  const [expanded, setExpanded] = useState(() => {
    const saved = localStorage.getItem(EXPANDED_KEY);
    return saved === null ? true : saved === 'true';
  });
  const [inboxOpen, setInboxOpen] = useState(true);
  const inboxSub = useDelayUnmount(inboxOpen && expanded, 260);
  const [channelsOpen, setChannelsOpen] = useState(() =>
    location.pathname.startsWith('/channels'),
  );
  const channelsSub = useDelayUnmount(channelsOpen && expanded, 260);

  useEffect(() => {
    localStorage.setItem(EXPANDED_KEY, String(expanded));
  }, [expanded]);

  useEffect(() => {
    if (location.pathname.startsWith('/channels')) setChannelsOpen(true);
  }, [location.pathname]);

  const params = new URLSearchParams(location.search);
  const activeChannel = params.get('channel') || 'All';
  const onInbox = location.pathname.startsWith('/conversations');
  const onChannels = location.pathname.startsWith('/channels');

  const selectChannel = (key) => {
    const q = key === 'All' ? '' : `?channel=${encodeURIComponent(key)}`;
    navigate(`/conversations${q}`);
  };

  const isChannelsSubActive = (sub) =>
    sub.end ? location.pathname === sub.to : location.pathname.startsWith(sub.to);

  return (
    <aside className={'rail' + (expanded ? ' expanded' : ' collapsed')}>
      <div className="rail-top">
        <div className="rail-brand-row">
          <button
            className="rail-brand-mark"
            title={expanded ? PRODUCT_NAME : 'Expand sidebar'}
            onClick={() => !expanded && setExpanded(true)}
          >
            <BrandMark />
          </button>
          <span className="rail-brand-text">{PRODUCT_NAME}</span>
          <button
            className="rail-collapse"
            title="Collapse sidebar"
            onClick={() => setExpanded(false)}
          >
            <Icon.SidebarToggle />
          </button>
        </div>
      </div>

      <nav className="rail-nav">
        {items.map((it) => {
          const I = it.icon;
          const isActive =
            it.to === '/conversations'
              ? onInbox
              : it.to === '/channels'
              ? onChannels
              : location.pathname.startsWith(it.to);
          const isInbox = it.label === 'Inbox';
          const isChannels = it.label === 'Channels';

          return (
            <div key={it.to} className="rail-group">
              <button
                className={'rail-row' + (isActive ? ' active' : '')}
                title={expanded ? undefined : it.label}
                onClick={() => {
                  if (isInbox && expanded) {
                    setInboxOpen((v) => !v);
                    if (!onInbox) navigate(it.to);
                  } else if (isChannels && expanded) {
                    setChannelsOpen((v) => !v);
                    if (!onChannels) navigate(it.to);
                  } else {
                    navigate(it.to);
                  }
                }}
              >
                <I />
                <span className="rail-label">{it.label}</span>
                {!!BADGE_BY_LABEL[it.label] && (
                  <span className="rail-badge" title={`${BADGE_BY_LABEL[it.label]} unread`}>
                    {BADGE_BY_LABEL[it.label] > 99 ? '99+' : BADGE_BY_LABEL[it.label]}
                  </span>
                )}
                {it.expandable && (
                  <Icon.Chevron
                    className={
                      'rail-caret' +
                      ((isInbox && inboxOpen) || (isChannels && channelsOpen) ? ' open' : '')
                    }
                  />
                )}
              </button>
              {isInbox && inboxSub.mounted && (
                <div className={'rail-sub' + (inboxSub.state === 'exiting' ? ' closing' : '')}>
                  {CHANNELS.map((c) => (
                    <button
                      key={c.key}
                      className={
                        'rail-sub-item' +
                        (onInbox && activeChannel === c.key ? ' active' : '')
                      }
                      onClick={() => selectChannel(c.key)}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              )}
              {isChannels && channelsSub.mounted && (
                <div
                  className={'rail-sub' + (channelsSub.state === 'exiting' ? ' closing' : '')}
                >
                  {CHANNEL_SUB.map((c) => (
                    <button
                      key={c.to}
                      className={
                        'rail-sub-item' + (isChannelsSubActive(c) ? ' active' : '')
                      }
                      onClick={() => navigate(c.to)}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="rail-bottom-area">
        <button
          className={'rail-row' + (location.pathname.startsWith('/settings') ? ' active' : '')}
          onClick={() => navigate('/settings')}
          title={expanded ? undefined : 'Settings'}
        >
          <Icon.Gear />
          <span className="rail-label">Settings</span>
        </button>
        <button
          className="rail-row"
          onClick={toggle}
          title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
        >
          {theme === 'dark' ? <Icon.Sun /> : <Icon.Moon />}
          <span className="rail-label">
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </span>
        </button>
      </div>
    </aside>
  );
}

export function BrandMark() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
      <path
        d="M3.5 5 L8 19 L12 10 L16 19 L20.5 5"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
