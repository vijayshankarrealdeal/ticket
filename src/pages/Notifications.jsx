import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { useNotifications, notificationsApi } from '../hooks/useNotifications.js';

const TYPE_META = {
  assign:    { icon: Icon.Ticket,    tone: 'primary', label: 'Assignments'  },
  reply:     { icon: Icon.Mail,      tone: 'info',    label: 'Replies'      },
  sla:       { icon: Icon.Clock,     tone: 'danger',  label: 'SLA'          },
  broadcast: { icon: Icon.Megaphone, tone: 'success', label: 'Broadcasts'   },
  member:    { icon: Icon.User,      tone: 'muted',   label: 'Team'         },
};

const FILTERS = [
  { key: 'all',       label: 'All'        },
  { key: 'unread',    label: 'Unread'     },
  { key: 'assign',    label: 'Assignments'},
  { key: 'reply',     label: 'Replies'    },
  { key: 'sla',       label: 'SLA'        },
  { key: 'broadcast', label: 'Broadcasts' },
];

function groupOf(ts) {
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  if (now - ts < day) return 'Today';
  if (now - ts < 2 * day) return 'Yesterday';
  if (now - ts < 7 * day) return 'This week';
  return 'Earlier';
}

export default function Notifications() {
  const items = useNotifications();
  const [filter, setFilter] = useState('all');
  const navigate = useNavigate();

  const filtered = useMemo(() => {
    if (filter === 'all') return items;
    if (filter === 'unread') return items.filter((n) => n.unread);
    return items.filter((n) => n.type === filter);
  }, [items, filter]);

  const grouped = useMemo(() => {
    const out = new Map();
    for (const n of filtered) {
      const g = groupOf(n.ts);
      if (!out.has(g)) out.set(g, []);
      out.get(g).push(n);
    }
    return [...out.entries()];
  }, [filtered]);

  const unreadCount = items.filter((n) => n.unread).length;

  const counts = useMemo(() => {
    const c = { all: items.length, unread: unreadCount };
    for (const n of items) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [items, unreadCount]);

  return (
    <>
      <TopBar
        left={
          <div className="top-bar-title-group">
            <div className="top-bar-title">Notifications</div>
            <div className="top-bar-sub">
              {unreadCount > 0 ? `${unreadCount} unread` : 'All caught up'} • {items.length} total
            </div>
          </div>
        }
      />
      <div className="page-scroll">
        <div className="notif-page-toolbar">
          <div className="notif-page-filters">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={'notif-filter' + (filter === f.key ? ' active' : '')}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
                <span className="notif-filter-count">{counts[f.key] || 0}</span>
              </button>
            ))}
          </div>
          <div className="notif-page-actions">
            <button
              className="btn secondary"
              onClick={notificationsApi.markAllRead}
              disabled={unreadCount === 0}
            >
              <Icon.Check2 /> Mark all read
            </button>
            <button
              className="btn secondary"
              onClick={notificationsApi.clearAll}
              disabled={items.length === 0}
            >
              <Icon.Close /> Clear all
            </button>
          </div>
        </div>

        <div className="notif-page-list">
          {filtered.length === 0 ? (
            <div className="notif-page-empty">
              <Icon.Bell />
              <div className="notif-page-empty-title">No notifications</div>
              <div className="notif-page-empty-sub">
                {filter === 'unread'
                  ? 'You have no unread notifications.'
                  : 'Nothing to show in this view.'}
              </div>
            </div>
          ) : (
            grouped.map(([group, rows]) => (
              <div key={group} className="notif-group">
                <div className="notif-group-head">{group}</div>
                {rows.map((n) => {
                  const meta = TYPE_META[n.type] || TYPE_META.member;
                  const IconCmp = meta.icon;
                  return (
                    <div
                      key={n.id}
                      className={'notif-page-item' + (n.unread ? ' unread' : '')}
                      onClick={() => {
                        notificationsApi.markRead(n.id);
                        if (n.link) navigate(n.link);
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <div className={'notif-type notif-type-' + meta.tone}>
                        <IconCmp />
                      </div>
                      <div className="notif-body">
                        <div className="notif-item-title">{n.title}</div>
                        <div className="notif-item-sub">{n.body}</div>
                        <div className="notif-time">
                          <span className="notif-type-tag">{meta.label}</span>
                          <span>•</span>
                          <span>{n.time}</span>
                        </div>
                      </div>
                      <div className="notif-item-side">
                        {n.unread && <span className="notif-dot" aria-label="Unread" />}
                        <button
                          className="notif-dismiss"
                          onClick={(e) => {
                            e.stopPropagation();
                            notificationsApi.dismiss(n.id);
                          }}
                          title="Dismiss"
                          aria-label="Dismiss"
                        >
                          <Icon.Close />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
