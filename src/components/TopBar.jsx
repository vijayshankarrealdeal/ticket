import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from './Icons.jsx';
import { useNotifications, notificationsApi } from '../hooks/useNotifications.js';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import GlobalSearch from './GlobalSearch.jsx';
import UserMenu from './UserMenu.jsx';

const TYPE_META = {
  assign:    { icon: Icon.Ticket,    tone: 'primary' },
  reply:     { icon: Icon.Mail,      tone: 'info'    },
  sla:       { icon: Icon.Clock,     tone: 'danger'  },
  broadcast: { icon: Icon.Megaphone, tone: 'success' },
  member:    { icon: Icon.User,      tone: 'muted'   },
};

export default function TopBar({ left, notifications }) {
  const items = useNotifications();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('all');
  const wrapRef = useRef(null);
  const navigate = useNavigate();
  const panel = useDelayUnmount(open, 220);

  const unreadCount = notifications ?? items.filter((n) => n.unread).length;
  const filtered = tab === 'unread' ? items.filter((n) => n.unread) : items;
  const visible = filtered.slice(0, 6);
  const hasMore = filtered.length > visible.length;

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const goSeeAll = () => {
    setOpen(false);
    navigate('/notifications');
  };

  return (
    <div className="top-bar">
      <div className="top-bar-left">{left}</div>
      <div className="top-bar-center">
        <GlobalSearch />
      </div>
      <div className="top-bar-right">
        <div className="notif-wrap" ref={wrapRef}>
          <button
            className="icon-btn"
            title="Notifications"
            aria-label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ''}`}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon.Bell />
            {unreadCount > 0 && (
              <span className="icon-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
            )}
          </button>
          {panel.mounted && (
            <div
              className={'notif-panel' + (panel.state === 'exiting' ? ' closing' : '')}
              role="dialog"
              aria-label="Notifications"
            >
              <div className="notif-head">
                <span className="notif-title">Notifications</span>
                <button
                  className="notif-mark"
                  onClick={notificationsApi.markAllRead}
                  disabled={unreadCount === 0}
                >
                  Mark all as read
                </button>
              </div>
              <div className="notif-tabs">
                <button
                  className={'notif-tab' + (tab === 'all' ? ' active' : '')}
                  onClick={() => setTab('all')}
                >
                  All
                  <span className="notif-tab-count">{items.length}</span>
                </button>
                <button
                  className={'notif-tab' + (tab === 'unread' ? ' active' : '')}
                  onClick={() => setTab('unread')}
                >
                  Unread
                  <span className="notif-tab-count">{unreadCount}</span>
                </button>
              </div>
              <div className="notif-list">
                {visible.length === 0 ? (
                  <div className="notif-empty">
                    <Icon.Bell />
                    <div>You're all caught up.</div>
                  </div>
                ) : (
                  visible.map((n) => (
                    <NotifRow
                      key={n.id}
                      n={n}
                      onClick={() => {
                        notificationsApi.markRead(n.id);
                        setOpen(false);
                        if (n.link) navigate(n.link);
                      }}
                      onDismiss={(e) => {
                        e.stopPropagation();
                        notificationsApi.dismiss(n.id);
                      }}
                    />
                  ))
                )}
              </div>
              <div className="notif-foot">
                <button className="notif-see-all" onClick={goSeeAll}>
                  See all notifications
                  {hasMore && <span className="notif-see-all-count">+{filtered.length - visible.length}</span>}
                </button>
              </div>
            </div>
          )}
        </div>
        <UserMenu />
      </div>
    </div>
  );
}

function NotifRow({ n, onClick, onDismiss }) {
  const meta = TYPE_META[n.type] || TYPE_META.member;
  const IconCmp = meta.icon;
  return (
    <div
      className={'notif-item' + (n.unread ? ' unread' : '')}
      onClick={onClick}
      role="button"
      tabIndex={0}
    >
      <div className={'notif-type notif-type-' + meta.tone}>
        <IconCmp />
      </div>
      <div className="notif-body">
        <div className="notif-item-title">{n.title}</div>
        <div className="notif-item-sub">{n.body}</div>
        <div className="notif-time">{n.time}</div>
      </div>
      <div className="notif-item-side">
        {n.unread && <span className="notif-dot" aria-label="Unread" />}
        <button
          className="notif-dismiss"
          onClick={onDismiss}
          title="Dismiss"
          aria-label="Dismiss notification"
        >
          <Icon.Close />
        </button>
      </div>
    </div>
  );
}
